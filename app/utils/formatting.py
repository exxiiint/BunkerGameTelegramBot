from __future__ import annotations

import html


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def display_name(username: str | None, first_name: str | None, tg_user_id: int | None = None) -> str:
    if username:
        return f"@{username}"
    if first_name:
        return first_name
    if tg_user_id is not None:
        return f"User {tg_user_id}"
    return "Игрок"


def mention(tg_user_id: int, username: str | None, first_name: str | None) -> str:
    name = display_name(username, first_name, tg_user_id)
    return f'<a href="tg://user?id={tg_user_id}">{escape(name)}</a>'
