from __future__ import annotations


def seats_in_bunker(players_count: int) -> int:
    if players_count < 2:
        raise ValueError("players_count must be >= 2")
    return players_count // 2


def exiled_total(players_count: int) -> int:
    return players_count - seats_in_bunker(players_count)


def votes_in_round(players_count: int, round_no: int) -> int:
    """Return number of votes in the given round for Base Mode.

    Rules are taken from the standard 4-16 players table:
    - Round 1: 0 votes
    - Round 2: 0 for 4-6; 1 for 7-14; 2 for 15-16
    - Round 3: 0 for 4; 1 for 5-12; 2 for 13-16
    - Round 4: 1 for 4-10; 2 for 11-16
    - Round 5: 1 for 4-8; 2 for 9-16

    We keep it explicit to avoid surprises.
    """
    if round_no not in (1, 2, 3, 4, 5):
        raise ValueError("round_no must be in 1..5")
    if not (4 <= players_count <= 16):
        raise ValueError("Base Mode supports 4..16 players")

    if round_no == 1:
        return 0
    if round_no == 2:
        if players_count <= 6:
            return 0
        if players_count <= 14:
            return 1
        return 2
    if round_no == 3:
        if players_count == 4:
            return 0
        if players_count <= 12:
            return 1
        return 2
    if round_no == 4:
        return 1 if players_count <= 10 else 2
    if round_no == 5:
        return 1 if players_count <= 8 else 2
    raise AssertionError("unreachable")


def total_votes(players_count: int) -> int:
    return sum(votes_in_round(players_count, r) for r in range(1, 6))
