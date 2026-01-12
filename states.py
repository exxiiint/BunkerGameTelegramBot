from aiogram.fsm.state import State, StatesGroup


class JoinLobbyState(StatesGroup):
    waiting_for_code = State()
