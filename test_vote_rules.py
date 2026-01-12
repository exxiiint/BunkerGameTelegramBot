import pytest

from app.services.vote_rules import votes_in_round, total_votes, exiled_total


@pytest.mark.parametrize("n", list(range(4, 17)))
def test_total_votes_equals_exiled_total(n: int) -> None:
    assert total_votes(n) == exiled_total(n)


@pytest.mark.parametrize(
    "n, expected_r2, expected_r3, expected_r4, expected_r5",
    [
        (4, 0, 0, 1, 1),
        (5, 0, 1, 1, 1),
        (6, 0, 1, 1, 1),
        (7, 1, 1, 1, 1),
        (8, 1, 1, 1, 1),
        (9, 1, 1, 1, 2),
        (10, 1, 1, 1, 2),
        (11, 1, 1, 2, 2),
        (12, 1, 1, 2, 2),
        (13, 1, 2, 2, 2),
        (14, 1, 2, 2, 2),
        (15, 2, 2, 2, 2),
        (16, 2, 2, 2, 2),
    ],
)
def test_votes_table(n: int, expected_r2: int, expected_r3: int, expected_r4: int, expected_r5: int) -> None:
    assert votes_in_round(n, 1) == 0
    assert votes_in_round(n, 2) == expected_r2
    assert votes_in_round(n, 3) == expected_r3
    assert votes_in_round(n, 4) == expected_r4
    assert votes_in_round(n, 5) == expected_r5
