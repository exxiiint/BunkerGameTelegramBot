from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Создать лобби", callback_data="m:create_lobby")
    kb.button(text="🔢 Присоединиться", callback_data="m:join_lobby")
    kb.button(text="🎮 Моя активная игра", callback_data="m:my_game")
    kb.button(text="👥 Моё лобби", callback_data="m:my_lobby")
    kb.adjust(1)
    return kb.as_markup()


def lobby_kb(lobby_id: int, is_owner: bool, is_ready: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=("✅ Готов" if not is_ready else "❌ Не готов"),
        callback_data=f"l:{lobby_id}:toggle_ready",
    )
    kb.button(text="🔄 Обновить", callback_data=f"l:{lobby_id}:refresh")
    if is_owner:
        kb.button(text="🚀 Старт игры", callback_data=f"l:{lobby_id}:start_game")
    kb.adjust(1)
    return kb.as_markup()


def bunker_choice_kb(game_id: int, closed_slots: list[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for slot in closed_slots:
        kb.button(
            text=f"Открыть карту бункера #{slot}", callback_data=f"g:{game_id}:open_bunker:{slot}"
        )
    kb.adjust(1)
    return kb.as_markup()


def reveal_choice_kb(game_id: int, categories: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for cat_code, cat_name in categories:
        kb.button(text=f"Раскрыть: {cat_name}", callback_data=f"g:{game_id}:reveal:{cat_code}")
    kb.adjust(1)
    return kb.as_markup()


def vote_kb(game_id: int, candidates: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """candidates: list of (seat_no, label)"""
    kb = InlineKeyboardBuilder()
    for seat_no, label in candidates:
        kb.button(text=label, callback_data=f"g:{game_id}:vote:{seat_no}")
    kb.adjust(2)
    return kb.as_markup()


def after_game_kb(game_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📜 История партии", callback_data=f"g:{game_id}:history")
    kb.button(text="🏠 В главное меню", callback_data="m:menu")
    kb.adjust(1)
    return kb.as_markup()
