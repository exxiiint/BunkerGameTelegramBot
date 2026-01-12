from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.db.base import session_scope
from app.keyboards import bunker_choice_kb, lobby_kb, main_menu_kb
from app.services import game_service, lobby_service
from app.utils.formatting import mention

router = Router()


def render_lobby_text(view: lobby_service.LobbyView) -> str:
    lines = []
    lines.append(f"👥 <b>Лобби</b> <code>{view.code}</code>")
    lines.append("")
    lines.append("Игроки:")
    for idx, (tg_user_id, username, is_ready) in enumerate(view.members, start=1):
        status = "✅" if is_ready else "⏳"
        name = f"@{username}" if username else str(tg_user_id)
        lines.append(f"{idx}. {status} {name}")
    lines.append("")
    lines.append("Нужно 4–16 игроков. Владелец может начать игру, когда все готовы.")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("l:"))
async def cb_lobby_actions(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    # l:{lobby_id}:{action}
    if len(parts) < 3:
        await callback.answer("Некорректная команда", show_alert=True)
        return
    lobby_id = int(parts[1])
    action = parts[2]

    if action == "toggle_ready":
        async with session_scope() as session:
            await lobby_service.ensure_user(
                session,
                tg_user_id=callback.from_user.id,
                username=callback.from_user.username,
                first_name=callback.from_user.first_name,
                last_name=callback.from_user.last_name,
            )
            try:
                is_ready = await lobby_service.toggle_ready(session, lobby_id, callback.from_user.id)
            except Exception:
                await callback.answer("Вы не в этом лобби", show_alert=True)
                return
            view = await lobby_service.lobby_view(session, lobby_id)
            is_owner = view.owner_tg_user_id == callback.from_user.id

        await callback.message.edit_text(
            render_lobby_text(view),
            reply_markup=lobby_kb(lobby_id, is_owner=is_owner, is_ready=is_ready),
            parse_mode="HTML",
        )
        await callback.answer("Готовность обновлена")
        return

    if action == "refresh":
        async with session_scope() as session:
            view = await lobby_service.lobby_view(session, lobby_id)
            is_owner = view.owner_tg_user_id == callback.from_user.id
            member_row = next((m for m in view.members if m[0] == callback.from_user.id), None)
            is_ready = bool(member_row[2]) if member_row else False

        await callback.message.edit_text(
            render_lobby_text(view),
            reply_markup=lobby_kb(lobby_id, is_owner=is_owner, is_ready=is_ready),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if action == "start_game":
        # Only owner can start; require ready; create game, deal cards, send messages
        async with session_scope() as session:
            try:
                game = await game_service.start_game(session, lobby_id=lobby_id, initiator_tg_user_id=callback.from_user.id)
            except game_service.GameError as e:
                await callback.answer(str(e), show_alert=True)
                return

            players = await game_service.get_game_players_full(session, game.id)
            bunker_cards = await game_service.get_bunker_cards(session, game.id)

            # Preload personal cards for each user
            player_cards_map = {}
            for _seat_no, tg_user_id, _username, _first_name, _status in players:
                cards = await game_service.get_player_cards(session, game.id, tg_user_id)
                player_cards_map[tg_user_id] = cards

        # Inform owner message in current chat
        await callback.message.edit_text(
            f"🚀 Игра началась! Game ID: <code>{game.id}</code>\n"
            f"Я разослал всем игрокам их карты и буду вести раунды.\n\n"
            f"Если кто-то не получил сообщение — попросите его нажать /start и заново зайти в бота.",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
        await callback.answer()

        # Broadcast start message to all players
        # active seat is 1 by our start_game() logic
        # Find active player mention
        active = next((p for p in players if p[0] == game.active_seat), None)
        active_name = mention(active[1], active[2], active[3]) if active else f"Игрок #{game.active_seat}"

        players_list = []
        for seat_no, tg_user_id, username, first_name, _status in players:
            players_list.append(f"{seat_no}. {mention(tg_user_id, username, first_name)}")
        players_text = "\n".join(players_list)

        for _seat_no, tg_user_id, _username, _first_name, _status in players:
            # Personal cards in private chat
            cards = player_cards_map.get(tg_user_id, [])
            card_lines = []
            for c in cards:
                card_lines.append(f"• <b>{c.title}</b>: {c.body}")
            cards_text = "\n".join(card_lines) if card_lines else "(нет карт)"

            await callback.bot.send_message(
                chat_id=tg_user_id,
                text=(
                    f"🎮 <b>Игра «Бункер» началась!</b>\n"
                    f"Game ID: <code>{game.id}</code>\n\n"
                    f"👥 Игроки:\n{players_text}\n\n"
                    f"🃏 <b>Ваши секретные карты</b> (никому не показывайте):\n{cards_text}\n\n"
                    f"Правило: в 1-м раунде раскрывается Профессия. В раундах 2–5 — по 1 карте на выбор в свой ход.\n"
                ),
                parse_mode="HTML",
            )

        # Start round 1 - bunker choice
        closed_slots = [bc.slot_no for bc in bunker_cards if not bc.is_opened]
        if active:
            await callback.bot.send_message(
                chat_id=active[1],
                text=(
                    f"🔦 <b>Раунд {game.round_no}</b>\n"
                    f"Вы активный игрок. Выберите, какую карту Бункера открыть:"
                ),
                reply_markup=bunker_choice_kb(game.id, closed_slots),
                parse_mode="HTML",
            )
        # Inform everyone whose turn
        for _seat_no, tg_user_id, _username, _first_name, _status in players:
            await callback.bot.send_message(
                chat_id=tg_user_id,
                text=f"🔦 Раунд {game.round_no} начинается. Активный игрок: {active_name}",
                parse_mode="HTML",
            )
        return

    await callback.answer("Неизвестное действие", show_alert=True)
