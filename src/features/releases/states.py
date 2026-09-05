"""FSM states for the releases feature.

Флоу линейный, состояние держится только в ``state.get_data()``:
``pipe_id``, ``project_id``, ``tree`` (кеш), ``rows`` (кеш). Явный
StatesGroup не нужен — все переходы по callback, без text-input.
"""
