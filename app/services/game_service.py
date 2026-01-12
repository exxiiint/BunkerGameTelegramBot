from __future__ import annotations

import random
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BunkerCard,
    Game,
    GameEvent,
    GamePhase,
    GamePlayer,
    GameStatus,
    Lobby,
    LobbyMember,
    LobbyStatus,
    PlayerCard,
    PlayerStatus,
    User,
    Vote,
)
from app.services.cards import (
    generate_bunker_cards,
    generate_character_cards,
)
from app.services.vote_rules import seats_in_bunker, votes_in_round


class GameError(Exception):
    pass


class NotFound(GameError):
    pass


class Forbidden(GameError):
    pass


class BadState(GameError):
    pass


@dataclass(frozen=True)
class PlayerInfo:
    seat_no: int
    tg_user_id: int
    username: str | None
    first_name: str | None
    status: PlayerStatus


@dataclass(frozen=True)
class OpenBunkerResult:
    opened: BunkerCard
    game: Game
    players: list[PlayerInfo]


@dataclass(frozen=True)
class RevealResult:
    revealed: PlayerCard
    game: Game
    players: list[PlayerInfo]
    circle_completed: bool
    voting_started: bool
    next_round_started: bool


@dataclass(frozen=True)
class VotingStart:
    game: Game
    players: list[PlayerInfo]
    voter_seats: list[int]
    candidate_seats: list[int]


@dataclass(frozen=True)
class VoteCastResult:
    game: Game
    players: list[PlayerInfo]
    all_votes_collected: bool
    tie: bool
    tie_candidate_seats: list[int] | None
    exiled_seat: int | None
    round_ended: bool
    game_finished: bool


async def _get_game_locked(session: AsyncSession, game_id: int) -> Game:
    game = await session.scalar(select(Game).where(Game.id == game_id).with_for_update())
    if game is None:
        raise NotFound("Game not found")
    return game


async def _get_players(session: AsyncSession, game_id: int) -> list[PlayerInfo]:
    rows = (
        await session.execute(
            select(
                GamePlayer.seat_no,
                GamePlayer.tg_user_id,
                User.username,
                User.first_name,
                GamePlayer.status,
            )
            .join(User, User.tg_user_id == GamePlayer.tg_user_id)
            .where(GamePlayer.game_id == game_id)
            .order_by(GamePlayer.seat_no.asc())
        )
    ).all()
    return [
        PlayerInfo(seat_no=r[0], tg_user_id=r[1], username=r[2], first_name=r[3], status=r[4])
        for r in rows
    ]


def _next_alive_seat(alive_seats: list[int], current_seat: int) -> int:
    if current_seat not in alive_seats:
        # if current seat is exiled, choose first alive after it in circular order
        candidates = [s for s in alive_seats if s > current_seat]
        return candidates[0] if candidates else alive_seats[0]
    idx = alive_seats.index(current_seat)
    return alive_seats[(idx + 1) % len(alive_seats)]


async def start_game(session: AsyncSession, lobby_id: int, initiator_tg_user_id: int) -> Game:
    lobby = await session.get(Lobby, lobby_id)
    if lobby is None:
        raise NotFound("Lobby not found")
    if lobby.status != LobbyStatus.OPEN:
        raise BadState("Lobby is not open")
    if lobby.owner_tg_user_id != initiator_tg_user_id:
        raise Forbidden("Only lobby owner can start the game")

    # Fetch members in join order
    members = (
        await session.execute(
            select(LobbyMember.tg_user_id, LobbyMember.is_ready, User.username, User.first_name)
            .join(User, User.tg_user_id == LobbyMember.tg_user_id)
            .where(LobbyMember.lobby_id == lobby_id)
            .order_by(LobbyMember.joined_at.asc())
        )
    ).all()

    if len(members) < 4 or len(members) > 16:
        raise BadState("Base Mode supports 4..16 players")

    # Require all ready (including owner)
    not_ready = [m for m in members if not m[1]]
    if not_ready:
        raise BadState("Не все игроки нажали «Готов»")

    players_count = len(members)
    bunker_seats = seats_in_bunker(players_count)

    game = Game(
        lobby_id=lobby_id,
        status=GameStatus.ACTIVE,
        phase=GamePhase.BUNKER_CHOICE,
        players_count=players_count,
        seats_in_bunker=bunker_seats,
        round_no=1,
        active_seat=1,
        turn_seat=1,
        vote_no=0,
        vote_attempt=0,
        vote_candidate_seats=None,
        last_exiled_seat=None,
    )
    session.add(game)
    await session.flush()  # game.id

    # Create players with seat numbers
    for idx, row in enumerate(members, start=1):
        tg_user_id = int(row[0])
        gp = GamePlayer(
            game_id=game.id, tg_user_id=tg_user_id, seat_no=idx, status=PlayerStatus.ALIVE
        )
        session.add(gp)

    # Generate cards
    seed = secrets.randbits(64)
    rng = random.Random(seed)

    per_seat = generate_character_cards(players_count, rng=rng)
    for seat_no, cards in per_seat.items():
        for c in cards:
            pc = PlayerCard(
                game_id=game.id,
                seat_no=seat_no,
                category=c.category,
                title=c.title,
                body=c.body,
                is_revealed=False,
                revealed_round=None,
                revealed_at=None,
            )
            session.add(pc)

    bunker_cards = generate_bunker_cards(rng=rng)
    for bc in bunker_cards:
        b = BunkerCard(
            game_id=game.id,
            slot_no=bc.slot_no,
            title=bc.title,
            body=bc.body,
            is_opened=False,
            opened_round=None,
            opened_at=None,
        )
        session.add(b)

    lobby.status = LobbyStatus.IN_GAME
    session.add(lobby)

    session.add(
        GameEvent(
            game_id=game.id,
            type="game_started",
            payload={"players_count": players_count, "seats_in_bunker": bunker_seats, "seed": seed},
        )
    )

    await session.flush()
    return game


async def open_bunker_card(
    session: AsyncSession, game_id: int, actor_tg_user_id: int, slot_no: int
) -> OpenBunkerResult:
    game = await _get_game_locked(session, game_id)
    if game.status != GameStatus.ACTIVE or game.phase != GamePhase.BUNKER_CHOICE:
        raise BadState("Сейчас нельзя открывать карту бункера")

    players = await _get_players(session, game_id)
    actor = next((p for p in players if p.tg_user_id == actor_tg_user_id), None)
    if actor is None:
        raise Forbidden("Вы не участник этой партии")

    if actor.status != PlayerStatus.ALIVE:
        raise Forbidden("Изгнанные не могут открывать карты бункера")

    if actor.seat_no != game.active_seat:
        raise Forbidden("Сейчас не ваш ход (карту бункера открывает активный игрок)")

    card = await session.scalar(
        select(BunkerCard)
        .where(BunkerCard.game_id == game_id, BunkerCard.slot_no == slot_no)
        .with_for_update()
    )
    if card is None:
        raise NotFound("Карта бункера не найдена")
    if card.is_opened:
        raise BadState("Эта карта бункера уже открыта")

    card.is_opened = True
    card.opened_round = game.round_no
    card.opened_at = datetime.now(tz=timezone.utc)
    session.add(card)

    # Move to reveal circle
    game.phase = GamePhase.REVEAL_TURN
    game.turn_seat = game.active_seat
    session.add(game)

    session.add(
        GameEvent(
            game_id=game.id,
            type="bunker_opened",
            payload={
                "slot_no": slot_no,
                "title": card.title,
                "body": card.body,
                "round_no": game.round_no,
                "by_seat": actor.seat_no,
            },
        )
    )

    await session.flush()
    return OpenBunkerResult(opened=card, game=game, players=players)


async def reveal_card(
    session: AsyncSession, game_id: int, actor_tg_user_id: int, category: Optional[str]
) -> RevealResult:
    game = await _get_game_locked(session, game_id)
    if game.status != GameStatus.ACTIVE or game.phase != GamePhase.REVEAL_TURN:
        raise BadState("Сейчас нельзя раскрывать карты")

    players = await _get_players(session, game_id)
    actor = next((p for p in players if p.tg_user_id == actor_tg_user_id), None)
    if actor is None:
        raise Forbidden("Вы не участник этой партии")
    if actor.status != PlayerStatus.ALIVE:
        raise Forbidden("Изгнанные не раскрывают карты до финала")
    if actor.seat_no != game.turn_seat:
        raise Forbidden("Сейчас не ваш ход")

    # Round 1: profession is mandatory
    if game.round_no == 1:
        category = "profession"
    if category is None:
        raise BadState("Не выбрана карта")

    # Find unrevealed card for this seat in that category
    card = await session.scalar(
        select(PlayerCard)
        .where(
            PlayerCard.game_id == game_id,
            PlayerCard.seat_no == actor.seat_no,
            PlayerCard.category == category,
        )
        .with_for_update()
    )
    if card is None:
        raise NotFound("Карта не найдена")
    if card.is_revealed:
        raise BadState("Эта карта уже раскрыта")

    card.is_revealed = True
    card.revealed_round = game.round_no
    card.revealed_at = datetime.now(tz=timezone.utc)
    session.add(card)

    session.add(
        GameEvent(
            game_id=game.id,
            type="card_revealed",
            payload={
                "seat_no": actor.seat_no,
                "category": card.category,
                "title": card.title,
                "body": card.body,
                "round_no": game.round_no,
            },
        )
    )

    # Advance turn
    alive_seats = [p.seat_no for p in players if p.status == PlayerStatus.ALIVE]
    alive_seats_sorted = sorted(alive_seats)
    next_seat = _next_alive_seat(alive_seats_sorted, actor.seat_no)

    circle_completed = False
    voting_started = False
    next_round_started = False

    # If next seat wraps to active seat, reveal circle ends
    if next_seat == game.active_seat:
        circle_completed = True
        # Determine votes for this round
        vcount = votes_in_round(game.players_count, game.round_no)
        if vcount == 0:
            # go to next round
            if game.round_no >= 5:
                # Shouldn't happen in Base Mode, but be safe
                game.status = GameStatus.FINISHED
                game.phase = GamePhase.FINISHED
                game.finished_at = datetime.now(tz=timezone.utc)
            else:
                game.round_no += 1
                game.active_seat = _next_alive_seat(alive_seats_sorted, game.active_seat)
                game.turn_seat = game.active_seat
                game.phase = GamePhase.BUNKER_CHOICE
                next_round_started = True
        else:
            # start voting
            game.phase = GamePhase.VOTING
            game.vote_no = 1
            game.vote_attempt = 1
            game.vote_candidate_seats = None
            voting_started = True
    else:
        game.turn_seat = next_seat

    session.add(game)
    await session.flush()

    return RevealResult(
        revealed=card,
        game=game,
        players=players,
        circle_completed=circle_completed,
        voting_started=voting_started,
        next_round_started=next_round_started,
    )


async def voting_start_info(session: AsyncSession, game_id: int) -> VotingStart:
    game = await session.get(Game, game_id)
    if game is None:
        raise NotFound("Game not found")
    if game.phase != GamePhase.VOTING:
        raise BadState("Голосование сейчас не активно")

    players = await _get_players(session, game_id)
    alive_seats = [p.seat_no for p in players if p.status == PlayerStatus.ALIVE]
    candidate_seats = game.vote_candidate_seats or alive_seats

    voter_seats = list(alive_seats)
    if game.last_exiled_seat is not None:
        # last exiled votes for all exiled
        voter_seats.append(game.last_exiled_seat)

    voter_seats = sorted(set(voter_seats))
    return VotingStart(
        game=game, players=players, voter_seats=voter_seats, candidate_seats=candidate_seats
    )


async def cast_vote(
    session: AsyncSession, game_id: int, actor_tg_user_id: int, target_seat: int
) -> VoteCastResult:
    game = await _get_game_locked(session, game_id)
    if game.status != GameStatus.ACTIVE or game.phase != GamePhase.VOTING:
        raise BadState("Сейчас не идёт голосование")

    players = await _get_players(session, game_id)
    actor = next((p for p in players if p.tg_user_id == actor_tg_user_id), None)
    if actor is None:
        raise Forbidden("Вы не участник этой партии")

    alive_seats = [p.seat_no for p in players if p.status == PlayerStatus.ALIVE]
    candidate_seats = game.vote_candidate_seats or alive_seats

    voter_seats = list(alive_seats)
    if game.last_exiled_seat is not None:
        voter_seats.append(game.last_exiled_seat)
    voter_seats = sorted(set(voter_seats))

    if actor.seat_no not in voter_seats:
        raise Forbidden("Вы не можете голосовать сейчас")

    if target_seat not in candidate_seats:
        raise BadState("Нельзя голосовать за этого кандидата")

    # Prevent voting for already exiled in full candidate set
    if target_seat not in alive_seats:
        raise BadState("Этот игрок уже изгнан")

    v = Vote(
        game_id=game.id,
        round_no=game.round_no,
        vote_no=game.vote_no,
        attempt=game.vote_attempt,
        voter_seat=actor.seat_no,
        target_seat=target_seat,
    )
    session.add(v)
    session.add(
        GameEvent(
            game_id=game.id,
            type="vote_cast",
            payload={
                "round_no": game.round_no,
                "vote_no": game.vote_no,
                "attempt": game.vote_attempt,
                "voter_seat": actor.seat_no,
                "target_seat": target_seat,
            },
        )
    )

    await session.flush()

    # Check if all votes collected
    votes_count = await session.scalar(
        select(func.count(Vote.id)).where(
            Vote.game_id == game.id,
            Vote.round_no == game.round_no,
            Vote.vote_no == game.vote_no,
            Vote.attempt == game.vote_attempt,
        )
    )
    all_votes_collected = votes_count == len(voter_seats)

    tie = False
    tie_candidate_seats: list[int] | None = None
    exiled_seat: int | None = None
    round_ended = False
    game_finished = False

    if all_votes_collected:
        # Finalize
        res = await _finalize_vote(session, game, players, voter_seats, candidate_seats)
        tie = res["tie"]
        tie_candidate_seats = res.get("tie_candidates")
        exiled_seat = res.get("exiled_seat")
        round_ended = res.get("round_ended", False)
        game_finished = res.get("game_finished", False)

    await session.flush()

    return VoteCastResult(
        game=game,
        players=players,
        all_votes_collected=all_votes_collected,
        tie=tie,
        tie_candidate_seats=tie_candidate_seats,
        exiled_seat=exiled_seat,
        round_ended=round_ended,
        game_finished=game_finished,
    )


async def _finalize_vote(
    session: AsyncSession,
    game: Game,
    players: list[PlayerInfo],
    voter_seats: list[int],
    candidate_seats: list[int],
) -> dict:
    # Count votes for current attempt
    rows = (
        await session.execute(
            select(Vote.target_seat, func.count(Vote.id))
            .where(
                Vote.game_id == game.id,
                Vote.round_no == game.round_no,
                Vote.vote_no == game.vote_no,
                Vote.attempt == game.vote_attempt,
            )
            .group_by(Vote.target_seat)
        )
    ).all()

    counts = {int(r[0]): int(r[1]) for r in rows}
    if not counts:
        raise BadState("No votes to finalize")

    max_votes = max(counts.values())
    top = sorted([seat for seat, c in counts.items() if c == max_votes])

    if len(top) > 1:
        # Tie
        if game.vote_attempt == 1:
            # Prepare revote among tied
            game.vote_attempt = 2
            game.vote_candidate_seats = top
            session.add(game)
            session.add(
                GameEvent(
                    game_id=game.id,
                    type="vote_tie",
                    payload={"round_no": game.round_no, "vote_no": game.vote_no, "candidates": top},
                )
            )
            return {"tie": True, "tie_candidates": top}
        else:
            # Second tie: random elimination among tied
            chosen = random.choice(top)
            await _exile_player(session, game, chosen)
            return await _after_exile(session, game)
    else:
        chosen = top[0]
        await _exile_player(session, game, chosen)
        return await _after_exile(session, game)


async def _exile_player(session: AsyncSession, game: Game, seat_no: int) -> None:
    gp = await session.scalar(
        select(GamePlayer)
        .where(GamePlayer.game_id == game.id, GamePlayer.seat_no == seat_no)
        .with_for_update()
    )
    if gp is None:
        raise NotFound("Player not found")
    if gp.status != PlayerStatus.ALIVE:
        raise BadState("Player already exiled")
    gp.status = PlayerStatus.EXILED
    gp.exiled_at = datetime.now(tz=timezone.utc)
    session.add(gp)

    game.last_exiled_seat = seat_no
    session.add(game)

    session.add(
        GameEvent(
            game_id=game.id,
            type="player_exiled",
            payload={"seat_no": seat_no, "round_no": game.round_no, "vote_no": game.vote_no},
        )
    )


async def _after_exile(session: AsyncSession, game: Game) -> dict:
    # Reset candidates for next vote unless we are entering revote
    game.vote_candidate_seats = None
    game.vote_attempt = 1  # default for next vote

    # Determine whether we need a second vote in this round
    vcount = votes_in_round(game.players_count, game.round_no)
    if game.vote_no < vcount:
        # Another vote in same round
        game.vote_no += 1
        game.vote_attempt = 1
        game.vote_candidate_seats = None
        game.phase = GamePhase.VOTING
        session.add(game)
        session.add(
            GameEvent(
                game_id=game.id,
                type="voting_next",
                payload={"round_no": game.round_no, "vote_no": game.vote_no},
            )
        )
        return {
            "tie": False,
            "exiled_seat": game.last_exiled_seat,
            "round_ended": False,
            "game_finished": False,
        }

    # Round voting finished
    players = await _get_players(session, game.id)
    alive_seats = sorted([p.seat_no for p in players if p.status == PlayerStatus.ALIVE])

    game.vote_no = 0
    game.vote_attempt = 0
    game.vote_candidate_seats = None

    # Finish or next round
    if game.round_no >= 5:
        game.status = GameStatus.FINISHED
        game.phase = GamePhase.FINISHED
        game.finished_at = datetime.now(tz=timezone.utc)
        session.add(game)
        session.add(
            GameEvent(game_id=game.id, type="game_finished", payload={"alive_seats": alive_seats})
        )
        return {
            "tie": False,
            "exiled_seat": game.last_exiled_seat,
            "round_ended": True,
            "game_finished": True,
        }

    # Next round begins with next alive after the one who started this round
    game.round_no += 1
    game.active_seat = _next_alive_seat(alive_seats, game.active_seat)
    game.turn_seat = game.active_seat
    game.phase = GamePhase.BUNKER_CHOICE
    session.add(game)
    session.add(
        GameEvent(
            game_id=game.id,
            type="round_started",
            payload={"round_no": game.round_no, "active_seat": game.active_seat},
        )
    )
    return {
        "tie": False,
        "exiled_seat": game.last_exiled_seat,
        "round_ended": True,
        "game_finished": False,
    }


async def get_player_cards(
    session: AsyncSession, game_id: int, tg_user_id: int
) -> list[PlayerCard]:
    players = await session.execute(
        select(GamePlayer.seat_no).where(
            GamePlayer.game_id == game_id, GamePlayer.tg_user_id == tg_user_id
        )
    )
    seat_no = players.scalar_one_or_none()
    if seat_no is None:
        raise Forbidden("Вы не участник этой партии")

    cards = (
        (
            await session.execute(
                select(PlayerCard)
                .where(PlayerCard.game_id == game_id, PlayerCard.seat_no == seat_no)
                .order_by(PlayerCard.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(cards)


async def get_game_by_lobby(session: AsyncSession, lobby_id: int) -> Optional[Game]:
    return await session.scalar(
        select(Game).where(Game.lobby_id == lobby_id).order_by(Game.created_at.desc()).limit(1)
    )


async def get_user_active_game(session: AsyncSession, tg_user_id: int) -> Optional[Game]:
    game_id = await session.scalar(
        select(GamePlayer.game_id)
        .join(Game, Game.id == GamePlayer.game_id)
        .where(GamePlayer.tg_user_id == tg_user_id, Game.status == GameStatus.ACTIVE)
        .order_by(Game.created_at.desc())
        .limit(1)
    )
    if game_id is None:
        return None
    return await session.get(Game, game_id)


async def get_game_events(session: AsyncSession, game_id: int, limit: int = 50) -> list[GameEvent]:
    return list(
        (
            await session.execute(
                select(GameEvent)
                .where(GameEvent.game_id == game_id)
                .order_by(GameEvent.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def get_bunker_cards(session: AsyncSession, game_id: int) -> list[BunkerCard]:
    return list(
        (
            await session.execute(
                select(BunkerCard)
                .where(BunkerCard.game_id == game_id)
                .order_by(BunkerCard.slot_no.asc())
            )
        )
        .scalars()
        .all()
    )


async def get_game_players_full(
    session: AsyncSession, game_id: int
) -> list[tuple[int, int, str | None, str | None, PlayerStatus]]:
    rows = (
        await session.execute(
            select(
                GamePlayer.seat_no,
                GamePlayer.tg_user_id,
                User.username,
                User.first_name,
                GamePlayer.status,
            )
            .join(User, User.tg_user_id == GamePlayer.tg_user_id)
            .where(GamePlayer.game_id == game_id)
            .order_by(GamePlayer.seat_no.asc())
        )
    ).all()
    return [(int(r[0]), int(r[1]), r[2], r[3], r[4]) for r in rows]
