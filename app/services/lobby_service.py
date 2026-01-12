from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lobby, LobbyMember, LobbyStatus, User


@dataclass(frozen=True)
class LobbyView:
    id: int
    code: str
    owner_tg_user_id: int
    status: LobbyStatus
    members: list[tuple[int, str | None, bool]]  # (tg_user_id, username, is_ready)


def _generate_code(length: int = 6) -> str:
    alphabet = string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def ensure_user(
    session: AsyncSession,
    tg_user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> User:
    user = await session.get(User, tg_user_id)
    if user is None:
        user = User(
            tg_user_id=tg_user_id, username=username, first_name=first_name, last_name=last_name
        )
        session.add(user)
        await session.flush()
        return user

    # Update basic fields opportunistically
    changed = False
    if username is not None and user.username != username:
        user.username = username
        changed = True
    if first_name is not None and user.first_name != first_name:
        user.first_name = first_name
        changed = True
    if last_name is not None and user.last_name != last_name:
        user.last_name = last_name
        changed = True
    if changed:
        session.add(user)
    return user


async def create_lobby(session: AsyncSession, owner_tg_user_id: int) -> Lobby:
    # Generate unique code
    for _ in range(20):
        code = _generate_code(6)
        exists = await session.scalar(select(Lobby.id).where(Lobby.code == code))
        if exists is None:
            break
    else:
        raise RuntimeError("Failed to generate unique lobby code")

    lobby = Lobby(code=code, owner_tg_user_id=owner_tg_user_id, status=LobbyStatus.OPEN)
    session.add(lobby)
    await session.flush()  # to get lobby.id

    # Owner joins lobby
    member = LobbyMember(lobby_id=lobby.id, tg_user_id=owner_tg_user_id, is_ready=False)
    session.add(member)
    await session.flush()

    return lobby


async def get_lobby_by_code(session: AsyncSession, code: str) -> Optional[Lobby]:
    return await session.scalar(select(Lobby).where(Lobby.code == code))


async def add_member(session: AsyncSession, lobby_id: int, tg_user_id: int) -> LobbyMember:
    existing = await session.scalar(
        select(LobbyMember).where(
            LobbyMember.lobby_id == lobby_id, LobbyMember.tg_user_id == tg_user_id
        )
    )
    if existing is not None:
        return existing
    member = LobbyMember(lobby_id=lobby_id, tg_user_id=tg_user_id, is_ready=False)
    session.add(member)
    await session.flush()
    return member


async def toggle_ready(session: AsyncSession, lobby_id: int, tg_user_id: int) -> bool:
    member = await session.scalar(
        select(LobbyMember).where(
            LobbyMember.lobby_id == lobby_id, LobbyMember.tg_user_id == tg_user_id
        )
    )
    if member is None:
        raise ValueError("Not a lobby member")
    member.is_ready = not member.is_ready
    session.add(member)
    await session.flush()
    return member.is_ready


async def lobby_view(session: AsyncSession, lobby_id: int) -> LobbyView:
    lobby = await session.get(Lobby, lobby_id)
    if lobby is None:
        raise ValueError("Lobby not found")

    rows = (
        await session.execute(
            select(LobbyMember.tg_user_id, User.username, LobbyMember.is_ready)
            .join(User, User.tg_user_id == LobbyMember.tg_user_id)
            .where(LobbyMember.lobby_id == lobby_id)
            .order_by(LobbyMember.joined_at.asc())
        )
    ).all()

    members = [(r[0], r[1], r[2]) for r in rows]
    return LobbyView(
        id=lobby.id,
        code=lobby.code,
        owner_tg_user_id=lobby.owner_tg_user_id,
        status=lobby.status,
        members=members,
    )


async def user_current_lobby(session: AsyncSession, tg_user_id: int) -> Optional[Lobby]:
    # The most recent OPEN lobby where user is a member
    lobby_id = await session.scalar(
        select(LobbyMember.lobby_id)
        .join(Lobby, Lobby.id == LobbyMember.lobby_id)
        .where(LobbyMember.tg_user_id == tg_user_id, Lobby.status == LobbyStatus.OPEN)
        .order_by(Lobby.created_at.desc())
        .limit(1)
    )
    if lobby_id is None:
        return None
    return await session.get(Lobby, lobby_id)
