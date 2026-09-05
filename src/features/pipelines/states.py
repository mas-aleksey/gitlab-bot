"""FSM states for the pipelines feature."""

from aiogram.fsm.state import State, StatesGroup


class PipelineState(StatesGroup):
    typing_branch = State()
