"""FSM states for the connections feature."""

from aiogram.fsm.state import State, StatesGroup


class AddConnectionState(StatesGroup):
    waiting_for_url = State()
    waiting_for_display_name = State()
    waiting_for_pat = State()


class RotatePatState(StatesGroup):
    waiting_for_pat = State()
