"""FSM states for the tags feature."""

from aiogram.fsm.state import State, StatesGroup


class TagState(StatesGroup):
    typing_ref = State()
    typing_name = State()
