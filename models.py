from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class LobbyStatus(str, enum.Enum):
    OPEN = "open"
    IN_GAME = "in_game"
    CLOSED = "closed"


class GameStatus(str, enum.Enum):
    ACTIVE = "active"
    FINISHED = "finished"


class GamePhase(str, enum.Enum):
    # Round begins: active player chooses and opens a bunker card
    BUNKER_CHOICE = "bunker_choice"
    # Reveal circle: current player reveals one of their character cards for this round
    REVEAL_TURN = "reveal_turn"
    # Voting: eligible voters cast secret votes
    VOTING = "voting"
    # Game ended
    FINISHED = "finished"


class PlayerStatus(str, enum.Enum):
    ALIVE = "alive"
    EXILED = "exiled"


class User(Base):
    __tablename__ = "users"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lobby_memberships: Mapped[list["LobbyMember"]] = relationship(back_populates="user")
    game_players: Mapped[list["GamePlayer"]] = relationship(back_populates="user")


class Lobby(Base):
    __tablename__ = "lobbies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    owner_tg_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_user_id"), index=True)
    status: Mapped[LobbyStatus] = mapped_column(Enum(LobbyStatus), default=LobbyStatus.OPEN, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped[User] = relationship()
    members: Mapped[list["LobbyMember"]] = relationship(back_populates="lobby", cascade="all, delete-orphan")
    games: Mapped[list["Game"]] = relationship(back_populates="lobby")


class LobbyMember(Base):
    __tablename__ = "lobby_members"
    __table_args__ = (UniqueConstraint("lobby_id", "tg_user_id", name="uq_lobby_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lobby_id: Mapped[int] = mapped_column(Integer, ForeignKey("lobbies.id", ondelete="CASCADE"), index=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_user_id", ondelete="CASCADE"), index=True)
    is_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lobby: Mapped[Lobby] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="lobby_memberships")


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lobby_id: Mapped[int] = mapped_column(Integer, ForeignKey("lobbies.id", ondelete="SET NULL"), nullable=True, index=True)

    status: Mapped[GameStatus] = mapped_column(Enum(GameStatus), default=GameStatus.ACTIVE, index=True)
    phase: Mapped[GamePhase] = mapped_column(Enum(GamePhase), default=GamePhase.BUNKER_CHOICE, index=True)

    players_count: Mapped[int] = mapped_column(Integer)
    seats_in_bunker: Mapped[int] = mapped_column(Integer)

    round_no: Mapped[int] = mapped_column(Integer, default=1)
    active_seat: Mapped[int] = mapped_column(Integer, default=1)  # who started the round
    turn_seat: Mapped[int] = mapped_column(Integer, default=1)  # whose turn right now in reveal circle

    # Voting state within a round
    vote_no: Mapped[int] = mapped_column(Integer, default=0)  # 0=not voting, 1 or 2
    vote_attempt: Mapped[int] = mapped_column(Integer, default=0)  # 0=not started, 1=first, 2=revote
    vote_candidate_seats: Mapped[Optional[list[int]]] = mapped_column(JSONB, nullable=True)

    last_exiled_seat: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    lobby: Mapped[Optional[Lobby]] = relationship(back_populates="games")
    players: Mapped[list["GamePlayer"]] = relationship(back_populates="game", cascade="all, delete-orphan")
    player_cards: Mapped[list["PlayerCard"]] = relationship(back_populates="game", cascade="all, delete-orphan")
    bunker_cards: Mapped[list["BunkerCard"]] = relationship(back_populates="game", cascade="all, delete-orphan")
    votes: Mapped[list["Vote"]] = relationship(back_populates="game", cascade="all, delete-orphan")
    events: Mapped[list["GameEvent"]] = relationship(back_populates="game", cascade="all, delete-orphan")


class GamePlayer(Base):
    __tablename__ = "game_players"
    __table_args__ = (UniqueConstraint("game_id", "seat_no", name="uq_game_seat"), UniqueConstraint("game_id", "tg_user_id", name="uq_game_user"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(Integer, ForeignKey("games.id", ondelete="CASCADE"), index=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_user_id", ondelete="CASCADE"), index=True)

    seat_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[PlayerStatus] = mapped_column(Enum(PlayerStatus), default=PlayerStatus.ALIVE, index=True)
    exiled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    game: Mapped[Game] = relationship(back_populates="players")
    user: Mapped[User] = relationship(back_populates="game_players")


class PlayerCard(Base):
    __tablename__ = "player_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(Integer, ForeignKey("games.id", ondelete="CASCADE"), index=True)
    seat_no: Mapped[int] = mapped_column(Integer, index=True)

    category: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)

    is_revealed: Mapped[bool] = mapped_column(Boolean, default=False)
    revealed_round: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    revealed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    game: Mapped[Game] = relationship(back_populates="player_cards")


class BunkerCard(Base):
    __tablename__ = "bunker_cards"
    __table_args__ = (UniqueConstraint("game_id", "slot_no", name="uq_bunker_slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(Integer, ForeignKey("games.id", ondelete="CASCADE"), index=True)
    slot_no: Mapped[int] = mapped_column(Integer)

    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)

    is_opened: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_round: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    game: Mapped[Game] = relationship(back_populates="bunker_cards")


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("game_id", "round_no", "vote_no", "attempt", "voter_seat", name="uq_vote_once"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(Integer, ForeignKey("games.id", ondelete="CASCADE"), index=True)

    round_no: Mapped[int] = mapped_column(Integer, index=True)
    vote_no: Mapped[int] = mapped_column(Integer, index=True)
    attempt: Mapped[int] = mapped_column(Integer, index=True)

    voter_seat: Mapped[int] = mapped_column(Integer, index=True)
    target_seat: Mapped[int] = mapped_column(Integer, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    game: Mapped[Game] = relationship(back_populates="votes")


class GameEvent(Base):
    __tablename__ = "game_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(Integer, ForeignKey("games.id", ondelete="CASCADE"), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    game: Mapped[Game] = relationship(back_populates="events")
