import pytest

from app.db.base import session_scope
from app.services import game_service, lobby_service
from app.services.vote_rules import seats_in_bunker


@pytest.mark.asyncio
@pytest.mark.integration
async def test_user_story_full_game_flow() -> None:
    # 4 players
    players = [
        (1001, "u1", "User1"),
        (1002, "u2", "User2"),
        (1003, "u3", "User3"),
        (1004, "u4", "User4"),
    ]

    async with session_scope() as session:
        # create users
        for tg_id, username, first_name in players:
            await lobby_service.ensure_user(session, tg_id, username, first_name, None)

        lobby = await lobby_service.create_lobby(session, owner_tg_user_id=players[0][0])
        for tg_id, *_ in players[1:]:
            await lobby_service.add_member(session, lobby.id, tg_id)

        # everyone ready
        for tg_id, *_ in players:
            # toggle_ready flips, so call once to set True
            await lobby_service.toggle_ready(session, lobby.id, tg_id)

        game = await game_service.start_game(session, lobby_id=lobby.id, initiator_tg_user_id=players[0][0])
        assert game.players_count == 4
        assert game.seats_in_bunker == seats_in_bunker(4)

    # Round 1
    async with session_scope() as session:
        await game_service.open_bunker_card(session, game.id, actor_tg_user_id=players[0][0], slot_no=1)
        # Reveal professions in seat order 1..4
        for tg_id, *_ in players:
            await game_service.reveal_card(session, game.id, actor_tg_user_id=tg_id, category=None)
        g = await session.get(game_service.Game, game.id)  # type: ignore
        assert g.round_no == 2
        assert g.phase == game_service.GamePhase.BUNKER_CHOICE

    # Round 2 (no voting for 4 players)
    async with session_scope() as session:
        await game_service.open_bunker_card(session, game.id, actor_tg_user_id=players[1][0], slot_no=2)  # active seat=2
        # Reveal one non-profession card for each alive
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[1][0], category="health")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[2][0], category="health")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[3][0], category="health")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[0][0], category="health")
        g = await session.get(game_service.Game, game.id)  # type: ignore
        assert g.round_no == 3
        assert g.phase == game_service.GamePhase.BUNKER_CHOICE

    # Round 3 (no voting)
    async with session_scope() as session:
        await game_service.open_bunker_card(session, game.id, actor_tg_user_id=players[2][0], slot_no=3)  # active seat=3
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[2][0], category="hobby")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[3][0], category="hobby")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[0][0], category="hobby")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[1][0], category="hobby")
        g = await session.get(game_service.Game, game.id)  # type: ignore
        assert g.round_no == 4
        assert g.phase == game_service.GamePhase.BUNKER_CHOICE

    # Round 4 (1 vote)
    async with session_scope() as session:
        await game_service.open_bunker_card(session, game.id, actor_tg_user_id=players[3][0], slot_no=4)  # active seat=4
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[3][0], category="trait")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[0][0], category="trait")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[1][0], category="trait")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[2][0], category="trait")
        g = await session.get(game_service.Game, game.id)  # type: ignore
        assert g.phase == game_service.GamePhase.VOTING
        assert g.vote_no == 1

        # Votes to exile seat 1 (tg 1001) by majority
        await game_service.cast_vote(session, game.id, actor_tg_user_id=players[0][0], target_seat=1)  # seat1 votes (can vote)
        await game_service.cast_vote(session, game.id, actor_tg_user_id=players[1][0], target_seat=1)
        await game_service.cast_vote(session, game.id, actor_tg_user_id=players[2][0], target_seat=1)
        res = await game_service.cast_vote(session, game.id, actor_tg_user_id=players[3][0], target_seat=2)  # seat4 votes different
        assert res.all_votes_collected is True
        assert res.exiled_seat == 1

        g = await session.get(game_service.Game, game.id)  # type: ignore
        assert g.round_no == 5
        assert g.phase == game_service.GamePhase.BUNKER_CHOICE
        assert g.last_exiled_seat == 1

    # Round 5 (1 vote, plus last exiled votes too)
    async with session_scope() as session:
        # active seat should be 2 now
        await game_service.open_bunker_card(session, game.id, actor_tg_user_id=players[1][0], slot_no=5)
        # Alive seats are 2,3,4 (seat1 exiled)
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[1][0], category="inventory")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[2][0], category="inventory")
        await game_service.reveal_card(session, game.id, actor_tg_user_id=players[3][0], category="inventory")
        g = await session.get(game_service.Game, game.id)  # type: ignore
        assert g.phase == game_service.GamePhase.VOTING

        # Voters: seats 2,3,4 + last exiled seat1
        # Exile seat2
        await game_service.cast_vote(session, game.id, actor_tg_user_id=players[0][0], target_seat=2)  # exiled seat1 votes
        await game_service.cast_vote(session, game.id, actor_tg_user_id=players[1][0], target_seat=2)
        await game_service.cast_vote(session, game.id, actor_tg_user_id=players[2][0], target_seat=2)
        res = await game_service.cast_vote(session, game.id, actor_tg_user_id=players[3][0], target_seat=3)
        assert res.all_votes_collected is True
        assert res.exiled_seat == 2
        assert res.game_finished is True

        g = await session.get(game_service.Game, game.id)  # type: ignore
        assert g.status == game_service.GameStatus.FINISHED
        assert g.phase == game_service.GamePhase.FINISHED
