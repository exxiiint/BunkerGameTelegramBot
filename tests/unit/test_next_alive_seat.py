from app.services.game_service import _next_alive_seat  # noqa: SLF001


def test_next_alive_seat_basic() -> None:
    alive = [1, 2, 3, 4]
    assert _next_alive_seat(alive, 1) == 2
    assert _next_alive_seat(alive, 4) == 1


def test_next_alive_seat_when_current_exiled() -> None:
    alive = [1, 3, 4, 6]
    assert _next_alive_seat(alive, 2) == 3
    assert _next_alive_seat(alive, 6) == 1
    assert _next_alive_seat(alive, 7) == 1
