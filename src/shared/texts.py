"""Cross-feature UI strings: navigation callbacks, main-menu buttons, errors.

Feature-local strings live in ``features/<name>/texts.py``. This module is
allowed to be imported from any feature; the reverse is forbidden.
"""

# ---------- navigation (cross-feature callbacks) ----------

NAV_WELCOME_CB = "nav:welcome"
NAV_PIPELINES_CB = "nav:pipelines"
NAV_MRS_CB = "nav:mrs"
NAV_TAGS_CB = "nav:tags"
NAV_RELEASES_CB = "nav:releases"
NAV_SETTINGS_CB = "nav:settings"
NAV_CONNECTIONS_CB = "nav:connections"
NAV_SWITCH_CB = "nav:switch"
NAV_ADD_CONNECTION_CB = "nav:add_connection"
NAV_INVITE_CB = "nav:invite"
NAV_POOL_CB = "nav:pool"
NAV_HELP_CB = "nav:help"

BTN_MAIN_MENU = "← Главное меню"
BTN_SETTINGS_MENU = "← Настройки"
BTN_MRS = "📋 MR"
BTN_PIPELINES = "🚀 Pipelines"
BTN_TAGS = "🏷 Tags"
BTN_RELEASES = "📦 Releases"
BTN_SETTINGS = "⚙️ Настройки"
BTN_SWITCH = "🔀 Сменить проект"
BTN_CONNECTIONS = "🔌 Мои подключения"
BTN_ADD_CONNECTION = "➕ Добавить подключение"
BTN_INVITE = "✉️ Выпустить инвайт"
BTN_POOL = "👥 Pool"
BTN_HELP = "❓ Справка"

SETTINGS_HEADER = "⚙️ <b>Настройки</b>"

# ---------- errors (shared by all features + global error handler) ----------

ERR_AUTH = "GitLab: неавторизован. Возможно PAT просрочен — /rotate_pat."
ERR_FORBIDDEN = "GitLab: доступ запрещён."
ERR_NOT_FOUND = "GitLab: не найдено."
ERR_CONFLICT = "GitLab: конфликт (уже существует / состояние не позволяет)."
ERR_BAD_REQUEST = "GitLab: некорректный запрос."
ERR_GITLAB_GENERIC = "Ошибка GitLab: {message}"
ERR_UNEXPECTED = "Что-то пошло не так. Попробуй позже."

# ---------- gates (middlewares) ----------

NO_POOL = "Тебя нет в pool. Попроси инвайт у админа."
NO_CONNECTION = "Нет активного подключения. /add_connection"
