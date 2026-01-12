from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.db.base import session_scope
from app.keyboards import main_menu_kb
from app.services import game_service, lobby_service
from app.states import JoinLobbyState

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        await lobby_service.ensure_user(
            session,
            tg_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
    await message.answer(
        "👋 Привет! Я бот для игры «Бункер» (базовый режим).\n\n"
        "Выбери действие:",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "m:menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🏠 Главное меню:", reply_markup=main_menu_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "m:create_lobby")
async def cb_create_lobby(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        await lobby_service.ensure_user(
            session,
            tg_user_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
        )
        lobby = await lobby_service.create_lobby(session, owner_tg_user_id=callback.from_user.id)

    await callback.message.edit_text(
        f"✅ Лобби создано!\n\n"
        f"🔢 Код лобби: <b>{lobby.code}</b>\n"
        f"Отправь этот код друзьям — они смогут присоединиться через кнопку «Присоединиться».\n\n"
        f"Когда все зайдут — нажмите «Готов», а владелец сможет нажать «Старт игры».",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "m:join_lobby")
async def cb_join_lobby(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(JoinLobbyState.waiting_for_code)
    await callback.message.edit_text(
        "🔢 Введите код лобби (6 цифр):",
        reply_markup=None,
    )
    await callback.answer()


@router.message(JoinLobbyState.waiting_for_code)
async def msg_join_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    if not (code.isdigit() and len(code) == 6):
        await message.answer("Код должен быть из 6 цифр. Попробуйте ещё раз:")
        return

    async with session_scope() as session:
        await lobby_service.ensure_user(
            session,
            tg_user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
        lobby = await lobby_service.get_lobby_by_code(session, code)
        if lobby is None or lobby.status != lobby_service.LobbyStatus.OPEN:
            await message.answer("Не нашёл открытое лобби с таким кодом 😕 Попробуйте другой код или создайте новое лобби.")
            return
        await lobby_service.add_member(session, lobby.id, message.from_user.id)

    await state.clear()
    await message.answer(
        f"✅ Вы присоединились к лобби <b>{code}</b>.\n"
        f"Нажмите «Моё лобби», чтобы отметить готовность.",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "m:my_lobby")
async def cb_my_lobby(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        await lobby_service.ensure_user(
            session,
            tg_user_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
        )
        lobby = await lobby_service.user_current_lobby(session, callback.from_user.id)
        if lobby is None:
            await callback.message.edit_text("У вас нет открытого лобби. Создайте новое или присоединитесь по коду.", reply_markup=main_menu_kb())
            await callback.answer()
            return

        view = await lobby_service.lobby_view(session, lobby.id)
        is_owner = view.owner_tg_user_id == callback.from_user.id
        member_row = next((m for m in view.members if m[0] == callback.from_user.id), None)
        is_ready = bool(member_row[2]) if member_row else False

    from app.handlers.lobby import render_lobby_text
    from app.keyboards import lobby_kb

    await callback.message.edit_text(
        render_lobby_text(view),
        reply_markup=lobby_kb(view.id, is_owner=is_owner, is_ready=is_ready),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "m:my_game")
async def cb_my_game(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        await lobby_service.ensure_user(
            session,
            tg_user_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
        )
        game = await game_service.get_user_active_game(session, callback.from_user.id)

    if game is None:
        await callback.message.edit_text("У вас нет активной игры.", reply_markup=main_menu_kb())
        await callback.answer()
        return

    from app.handlers.game import render_game_brief
    await callback.message.edit_text(render_game_brief(game), reply_markup=main_menu_kb(), parse_mode="HTML")
    await callback.answer()
