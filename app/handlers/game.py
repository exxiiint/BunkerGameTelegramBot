from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.db.base import session_scope
from app.keyboards import after_game_kb, bunker_choice_kb, reveal_choice_kb, vote_kb
from app.services import game_service
from app.services.cards import CHARACTER_CATEGORIES
from app.utils.formatting import display_name, escape, mention

router = Router()


def render_game_brief(game) -> str:
    return (
        f"🎮 <b>Активная партия</b>\n"
        f"ID: <code>{game.id}</code>\n"
        f"Раунд: <b>{game.round_no}</b>\n"
        f"Фаза: <b>{game.phase.value}</b>\n"
        f"Игроков: <b>{game.players_count}</b>, мест в бункере: <b>{game.seats_in_bunker}</b>"
    )


def _seat_to_user(players: list[game_service.PlayerInfo]) -> dict[int, game_service.PlayerInfo]:
    return {p.seat_no: p for p in players}


async def _send_reveal_prompt(bot, game, players: list[game_service.PlayerInfo]) -> None:
    seat_map = _seat_to_user(players)
    current = seat_map.get(game.turn_seat)
    if not current:
        return

    # Determine which categories are still unrevealed for that seat
    async with session_scope() as session:
        cards = await game_service.get_player_cards(session, game.id, current.tg_user_id)

    if game.round_no == 1:
        categories = [("profession", "Профессия")]
    else:
        categories = [(c.category, c.title) for c in cards if not c.is_revealed]

        # keep stable order
        order = [code for code, _ in CHARACTER_CATEGORIES]
        categories.sort(key=lambda x: order.index(x[0]) if x[0] in order else 999)

    if not categories:
        # Should not happen
        await bot.send_message(current.tg_user_id, "У вас нет карт для раскрытия (похоже на баг). Напишите /start.")
        return

    await bot.send_message(
        chat_id=current.tg_user_id,
        text=(
            f"🗣 <b>Ваш ход</b> (Раунд {game.round_no}).\n"
            f"Раскройте одну карту персонажа."
        ),
        reply_markup=reveal_choice_kb(game.id, categories),
        parse_mode="HTML",
    )


async def _broadcast(bot, players: list[game_service.PlayerInfo], text: str, **kwargs) -> None:
    for p in players:
        try:
            await bot.send_message(chat_id=p.tg_user_id, text=text, **kwargs)
        except Exception:
            # ignore send errors (user blocked bot etc.)
            pass


async def _send_voting_ballots(bot, game_id: int) -> None:
    async with session_scope() as session:
        info = await game_service.voting_start_info(session, game_id)
        game = info.game
        players = info.players

    seat_map = _seat_to_user(players)

    candidates_labels = []
    for seat in info.candidate_seats:
        p = seat_map.get(seat)
        if p:
            label = f"#{seat} {display_name(p.username, p.first_name, p.tg_user_id)}"
        else:
            label = f"#{seat}"
        candidates_labels.append((seat, label))

    for voter_seat in info.voter_seats:
        voter = seat_map.get(voter_seat)
        if not voter:
            continue
        await bot.send_message(
            chat_id=voter.tg_user_id,
            text=(
                f"🗳 <b>Голосование</b> (Раунд {game.round_no}, голосование {game.vote_no}, попытка {game.vote_attempt}).\n"
                f"Выберите кандидата на изгнание:"
            ),
            reply_markup=vote_kb(game.id, candidates_labels),
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("g:"))
async def cb_game_actions(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    # g:{game_id}:{action}:{param}
    if len(parts) < 3:
        await callback.answer("Некорректная команда", show_alert=True)
        return
    game_id = int(parts[1])
    action = parts[2]
    param = parts[3] if len(parts) > 3 else None

    if action == "open_bunker":
        slot_no = int(param or "0")
        async with session_scope() as session:
            try:
                res = await game_service.open_bunker_card(session, game_id=game_id, actor_tg_user_id=callback.from_user.id, slot_no=slot_no)
            except game_service.GameError as e:
                await callback.answer(str(e), show_alert=True)
                return

        # Announce opened bunker card
        opener = next((p for p in res.players if p.seat_no == res.game.active_seat), None)
        opener_name = mention(opener.tg_user_id, opener.username, opener.first_name) if opener else f"Игрок #{res.game.active_seat}"

        await _broadcast(
            callback.bot,
            res.players,
            text=(
                f"🔦 <b>Карта Бункера открыта</b> (Раунд {res.game.round_no})\n"
                f"Открыл: {opener_name}\n\n"
                f"<b>{escape(res.opened.title)}</b>: {escape(res.opened.body)}\n\n"
                f"🗣 Начинается круг раскрытия карт."
            ),
            parse_mode="HTML",
        )

        # Prompt first player in reveal circle
        await _send_reveal_prompt(callback.bot, res.game, res.players)

        await callback.answer()
        return

    if action == "reveal":
        category = param
        async with session_scope() as session:
            try:
                res = await game_service.reveal_card(session, game_id=game_id, actor_tg_user_id=callback.from_user.id, category=category)
            except game_service.GameError as e:
                await callback.answer(str(e), show_alert=True)
                return

        seat_map = _seat_to_user(res.players)
        revealer = seat_map.get(res.revealed.seat_no)
        revealer_name = mention(revealer.tg_user_id, revealer.username, revealer.first_name) if revealer else f"Игрок #{res.revealed.seat_no}"

        await _broadcast(
            callback.bot,
            res.players,
            text=(
                f"🃏 <b>Карта раскрыта</b> (Раунд {res.game.round_no})\n"
                f"{revealer_name} раскрывает: <b>{escape(res.revealed.title)}</b> — {escape(res.revealed.body)}\n\n"
                f"(Игроку даётся до 30 секунд на аргументы — обсуждайте голосом/в чате.)"
            ),
            parse_mode="HTML",
        )

        # If voting started, announce + send ballots
        if res.voting_started:
            await _broadcast(
                callback.bot,
                res.players,
                text=(
                    "🗳 <b>Круг завершён.</b>\n"
                    "Общее обсуждение ~1 минута, затем голосование начнётся в личке у каждого.\n"
                    "(Вы можете сразу голосовать — бот посчитает, когда все проголосуют.)"
                ),
                parse_mode="HTML",
            )
            await _send_voting_ballots(callback.bot, game_id)
        elif res.next_round_started:
            # New round started, prompt active player to open bunker card
            async with session_scope() as session:
                bunker_cards = await game_service.get_bunker_cards(session, game_id)
                players = await game_service.get_game_players_full(session, game_id)
            closed_slots = [bc.slot_no for bc in bunker_cards if not bc.is_opened]
            active = next((p for p in players if p[0] == res.game.active_seat), None)
            if active:
                await callback.bot.send_message(
                    chat_id=active[1],
                    text=(
                        f"🔦 <b>Раунд {res.game.round_no}</b> начинается.\n"
                        f"Вы активный игрок. Выберите карту бункера:"
                    ),
                    reply_markup=bunker_choice_kb(game_id, closed_slots),
                    parse_mode="HTML",
                )
            # Inform all
            active_name = mention(active[1], active[2], active[3]) if active else f"Игрок #{res.game.active_seat}"
            # res.players is from previous round, still ok; but update statuses might have changed? no exiles here.
            await _broadcast(callback.bot, res.players, text=f"🔦 Раунд {res.game.round_no} начинается. Активный игрок: {active_name}", parse_mode="HTML")
        else:
            # Continue reveal circle, prompt next player
            await _send_reveal_prompt(callback.bot, res.game, res.players)

        await callback.answer()
        return

    if action == "vote":
        target_seat = int(param or "0")
        async with session_scope() as session:
            try:
                res = await game_service.cast_vote(session, game_id=game_id, actor_tg_user_id=callback.from_user.id, target_seat=target_seat)
            except game_service.GameError as e:
                await callback.answer(str(e), show_alert=True)
                return

        await callback.answer("Голос принят ✅")

        if not res.all_votes_collected:
            return

        # If tie -> announce and resend ballots for revote
        if res.tie and res.tie_candidate_seats:
            await _broadcast(
                callback.bot,
                res.players,
                text=(
                    f"⚠️ <b>Ничья в голосовании</b> (Раунд {res.game.round_no}, голосование {res.game.vote_no}).\n"
                    f"Кандидаты: {', '.join('#'+str(s) for s in res.tie_candidate_seats)}\n\n"
                    f"Дайте кандидатам по 30 секунд на защиту, затем переголосуйте (бот уже разослал бюллетени).\n"
                ),
                parse_mode="HTML",
            )
            await _send_voting_ballots(callback.bot, game_id)
            return

        # Someone exiled
        if res.exiled_seat is not None:
            seat_map = _seat_to_user(res.players)
            ex = seat_map.get(res.exiled_seat)
            ex_name = mention(ex.tg_user_id, ex.username, ex.first_name) if ex else f"Игрок #{res.exiled_seat}"

            await _broadcast(
                callback.bot,
                res.players,
                text=(
                    f"🚫 <b>Игрок изгнан</b>\n"
                    f"Изгнан: {ex_name}\n"
                ),
                parse_mode="HTML",
            )

        # If game finished -> reveal all remaining cards and show summary
        if res.game_finished:
            async with session_scope() as session:
                players = await game_service.get_game_players_full(session, game_id)
                # collect all cards by seat
                cards_rows = {}
                for seat_no, tg_user_id, username, first_name, status in players:
                    cards = await game_service.get_player_cards(session, game_id, tg_user_id)
                    cards_rows[seat_no] = (tg_user_id, username, first_name, status, cards)

            # survivors
            alive = [seat for seat, (_, _, _, status, _) in cards_rows.items() if status == game_service.PlayerStatus.ALIVE]
            alive_sorted = sorted(alive)
            survivors_text = ", ".join(f"#{s}" for s in alive_sorted)

            # Broadcast final
            await _broadcast(
                callback.bot,
                [game_service.PlayerInfo(seat_no=s, tg_user_id=cards_rows[s][0], username=cards_rows[s][1], first_name=cards_rows[s][2], status=cards_rows[s][3]) for s in cards_rows],
                text=(
                    f"🏁 <b>Игра завершена!</b>\n"
                    f"В бункер попали: {survivors_text}\n\n"
                    f"Сейчас бот раскроет все оставшиеся карты для обсуждения финала."
                ),
                parse_mode="HTML",
            )

            # Send full reveals per player to everyone (could be spammy but OK for small games)
            for seat_no, (tg_user_id, username, first_name, _status, cards) in cards_rows.items():
                lines = []
                for c in cards:
                    mark = "✅" if c.is_revealed else "🟦"
                    lines.append(f"{mark} <b>{escape(c.title)}</b>: {escape(c.body)}")
                await _broadcast(
                    callback.bot,
                    [game_service.PlayerInfo(seat_no=s, tg_user_id=cards_rows[s][0], username=cards_rows[s][1], first_name=cards_rows[s][2], status=cards_rows[s][3]) for s in cards_rows],
                    text=(f"🧾 <b>Карты игрока #{seat_no}</b> ({mention(tg_user_id, username, first_name)}):\n" + "\n".join(lines)),
                    parse_mode="HTML",
                )

            # One more message with history button
            await _broadcast(
                callback.bot,
                [game_service.PlayerInfo(seat_no=s, tg_user_id=cards_rows[s][0], username=cards_rows[s][1], first_name=cards_rows[s][2], status=cards_rows[s][3]) for s in cards_rows],
                text="📜 Можно открыть историю партии.",
                reply_markup=after_game_kb(game_id),
            )
            return

        # If another vote in the same round is active
        async with session_scope() as session:
            game = await session.get(game_service.Game, game_id)  # type: ignore
        # We can't import Game directly from service, so just try sending ballots if phase is voting
        # (safe even if not)
        try:
            async with session_scope() as session:
                g = await session.get(game_service.Game, game_id)  # type: ignore
        except Exception:
            g = None

        # Just attempt to resend ballots if still voting
        if g is not None and getattr(g, "phase", None) == game_service.GamePhase.VOTING:
            await _broadcast(
                callback.bot,
                res.players,
                text=(f"🗳 Следующее голосование (Раунд {res.game.round_no}, голосование {res.game.vote_no}). Бот разослал бюллетени."),
            )
            await _send_voting_ballots(callback.bot, game_id)
            return

        # Otherwise next round started -> prompt active player for bunker choice
        async with session_scope() as session:
            game = await session.get(game_service.Game, game_id)  # type: ignore
            bunker_cards = await game_service.get_bunker_cards(session, game_id)
            players_full = await game_service.get_game_players_full(session, game_id)
        if game is not None and getattr(game, "phase", None) == game_service.GamePhase.BUNKER_CHOICE:
            closed_slots = [bc.slot_no for bc in bunker_cards if not bc.is_opened]
            active = next((p for p in players_full if p[0] == game.active_seat), None)
            if active:
                await callback.bot.send_message(
                    chat_id=active[1],
                    text=(
                        f"🔦 <b>Раунд {game.round_no}</b> начинается.\n"
                        f"Вы активный игрок. Выберите карту бункера:"
                    ),
                    reply_markup=bunker_choice_kb(game_id, closed_slots),
                    parse_mode="HTML",
                )
            active_name = mention(active[1], active[2], active[3]) if active else f"Игрок #{game.active_seat}"
            await _broadcast(callback.bot, res.players, text=f"🔦 Раунд {game.round_no} начинается. Активный игрок: {active_name}", parse_mode="HTML")
        return

    if action == "history":
        async with session_scope() as session:
            events = await game_service.get_game_events(session, game_id, limit=30)

        lines = ["📜 <b>История партии</b> (последние 30 событий):"]
        for e in reversed(events):
            ts = e.created_at.strftime("%H:%M:%S")
            lines.append(f"• <code>{ts}</code> — <b>{escape(e.type)}</b> {escape(str(e.payload))}")
        await callback.message.answer("\n".join(lines), parse_mode="HTML")
        await callback.answer()
        return

    await callback.answer("Неизвестное действие", show_alert=True)


@router.message(Command("cards"))
async def cmd_cards(message: Message) -> None:
    async with session_scope() as session:
        game = await game_service.get_user_active_game(session, message.from_user.id)
        if game is None:
            await message.answer("У вас нет активной игры.")
            return
        cards = await game_service.get_player_cards(session, game.id, message.from_user.id)

    lines = [f"🃏 <b>Ваши карты</b> (Game ID: <code>{game.id}</code>):"]
    for c in cards:
        mark = "✅" if c.is_revealed else "🟦"
        lines.append(f"{mark} <b>{escape(c.title)}</b>: {escape(c.body)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    async with session_scope() as session:
        game = await game_service.get_user_active_game(session, message.from_user.id)
        if game is None:
            await message.answer("У вас нет активной игры.")
            return
        events = await game_service.get_game_events(session, game.id, limit=30)

    lines = ["📜 <b>История партии</b> (последние 30 событий):"]
    for e in reversed(events):
        ts = e.created_at.strftime("%H:%M:%S")
        lines.append(f"• <code>{ts}</code> — <b>{escape(e.type)}</b> {escape(str(e.payload))}")
    await message.answer("\n".join(lines), parse_mode="HTML")
