"""Help-local UI strings: sections for guests and registered users."""

HELP_HEADER = "<b>Справка</b>\n\n"

HELP_GUEST = (
    "Ты пока не в pool — попроси у админа инвайт-код и отправь\n"
    "<code>/start &lt;код&gt;</code>.\n\n"
    "<b>Доступные команды:</b>\n"
    "• /start — приветствие и главное меню\n"
    "• /help — эта справка"
)

HELP_USER = (
    "• /start — главное меню\n"
    "\n"
    "<b>Подключения</b>\n"
    "• /add_connection — добавить GitLab-подключение (URL + PAT)\n"
    "• /connections — список подключений, смена default, удаление, rotate PAT\n"
    "\n"
    "<b>Работа с GitLab</b>\n"
    "• /pipelines — запуск pipeline на ветке или drill в manual-джобы\n"
    "• /mrs — просмотр открытых MR, approve/unapprove/merge\n"
    "• /tags — список тегов и создание нового\n"
    "\n"
    "<b>Pool</b>\n"
    "• /invite — выпустить инвайт-код (deep-link)\n"
    "• /pool — участники, подключённые к текущему проекту (/pool all — все)\n"
    "\n"
    "<b>Прочее</b>\n"
    "• /cancel — прервать ввод (FSM)\n"
    "• /help — эта справка"
)

HELP_ADMIN = (
    "\n"
    "\n<b>Админ</b>"
    "\n• /audit — что делали в активном проекте за 30 дней (/audit all — везде)"
    "\n• /audit @username — то же по одному человеку из пула"
)

# Автокомплит по «/» в клиенте (``set_my_commands``). Держим рядом со справкой,
# чтобы список и текст /help правились в одном файле.
MENU_COMMANDS = [
    ("start", "главное меню"),
    ("pipelines", "запустить pipeline или manual-джобы"),
    ("mrs", "открытые MR: approve, merge"),
    ("tags", "теги: список и создание"),
    ("release", "релиз-джобы в tag-пайплайне"),
    ("connections", "подключения: default, удаление, rotate PAT"),
    ("add_connection", "добавить GitLab-подключение"),
    ("pool", "кто ещё в активном проекте (/pool all — все)"),
    ("invite", "выпустить инвайт-код"),
    ("audit", "что делали в активном проекте — только админ"),
    ("cancel", "прервать ввод"),
    ("help", "справка"),
]
