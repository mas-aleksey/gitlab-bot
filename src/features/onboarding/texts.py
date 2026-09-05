"""Onboarding-local UI strings: welcome, invites, pool."""

WELCOME_GUEST = (
    "Привет. Ты пока не в pool — попроси у админа инвайт-код "
    "и отправь /start &lt;код&gt;."
)
WELCOME_BACK = "С возвращением, {name}."
WELCOME_MENU = (
    "Привет, <b>{name}</b>!\n"
    "Бот для запуска pipelines и manual-джоб в GitLab.\n"
    "\n"
    "{conn_line}\n"
    "\n"
    "Полный список команд — /help"
)
WELCOME_CONN_ACTIVE = "Активное подключение: <b>{display_name}</b> — <code>{scope_path}</code>"
WELCOME_CONN_NONE = "Нет активного подключения. Нажми «Добавить подключение»."

SWITCH_ASK = "Активный проект — один тап:"
SWITCH_ROW = "{mark}{display_name} — {scope_path}"
SWITCH_MARK_ACTIVE = "★ "
SWITCH_MARK_IDLE = ""
SWITCH_OK = "★ {display_name}"
SWITCH_EMPTY = "Подключений нет. /add_connection"

SEED_ADMIN_REGISTERED = "Ты первый — назначаю тебя админом. /add_connection чтобы начать."
INVITE_ACCEPTED = "Инвайт принят. /add_connection чтобы подключить GitLab."
INVITE_INVALID = "Инвайт-код неверен или просрочен."
INVITE_USED = "Этот инвайт-код уже использован."
ALREADY_REGISTERED = "Ты уже в pool."

INVITE_MESSAGE = (
    "Инвайт-код (валиден до конца UTC-суток):\n"
    "<code>{code}</code>\n\n"
    "Deep-link: {deeplink}"
)
POOL_EMPTY = "Pool пуст."
POOL_HEADER = "В pool {count} чел.:"
POOL_SCOPE_HEADER = "В <code>{scope_path}</code> {count} чел.:"
POOL_SCOPE_EMPTY = (
    "В <code>{scope_path}</code> пока никого, кроме тебя.\n"
    "Весь pool — /pool all"
)
POOL_ROW = "• {name} (id <code>{tg_user_id}</code>){admin_tag}"
POOL_ROW_SCOPE = " — <code>{scope_path}</code>"
POOL_ADMIN_TAG = " — admin"
POOL_NO_CONNECTION = (
    "Нет активного подключения — показать некого.\n"
    "/add_connection чтобы подключиться, или /pool all — весь pool."
)
POOL_ALL_HINT = "\n\nТолько по текущему проекту — /pool"
