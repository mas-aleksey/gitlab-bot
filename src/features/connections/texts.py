"""Connections-local UI strings: add/list/view/rotate/delete."""

CONN_ASK_URL = (
    "Пришли ссылку на группу или проект GitLab.\n"
    "Пример: <code>https://gitlab.com/mygroup/subgroup</code>"
)
CONN_ASK_DISPLAY_NAME = "Как назвать это подключение? (короткое имя, для тебя)"
CONN_ASK_PAT = (
    "Пришли PAT (Personal Access Token) c правами <b>api</b>.\n"
    "Создать: {base_url}/-/user_settings/personal_access_tokens\n\n"
    "Сообщение с токеном я удалю сразу после проверки."
)
CONN_URL_BAD = "Не смог распарсить ссылку: {reason}"
CONN_DUPLICATE = "Такое подключение (host + путь) уже есть."
CONN_REGISTERED = (
    "Готово: <b>{display_name}</b> → <code>{scope_path}</code> ({scope_kind}).\n"
    "GitLab user: <code>{gitlab_username}</code>.{default_note}"
)
CONN_MADE_DEFAULT = "\n\nВыбрано как <b>default</b>."
CONN_CANCELLED = "Отменено."

CONN_LIST_EMPTY = "У тебя пока нет подключений. /add_connection"
CONN_LIST_HEADER = "Твои подключения:"
CONN_LIST_ROW = "{default_mark}{display_name} — <code>{scope_path}</code>"
CONN_DEFAULT_MARK = "★ "
CONN_VIEW = (
    "<b>{display_name}</b>{default_tag}\n"
    "Scope: <code>{scope_path}</code> ({scope_kind})\n"
    "Host: {base_url}\n"
    "GitLab user: <code>{gitlab_username}</code>"
)
CONN_VIEW_DEFAULT_TAG = " ★"
CONN_BTN_SET_DEFAULT = "Сделать default"
CONN_BTN_DELETE = "Удалить"
CONN_BTN_ROTATE_PAT = "Rotate PAT"
CONN_BTN_BACK = "К списку"
CONN_BTN_CONFIRM_DELETE = "Да, удалить"
CONN_BTN_CANCEL = "Отмена"
CONN_SET_DEFAULT_OK = "Default: <b>{display_name}</b>."
CONN_DELETE_ASK = "Удалить <b>{display_name}</b>?"
CONN_DELETED = "Удалено: {display_name}."
CONN_DELETED_NEW_DEFAULT = "\nНовый default: <b>{display_name}</b>."
CONN_DELETED_NO_DEFAULT = "\nАктивных подключений не осталось."
CONN_NOT_FOUND = "Подключение не найдено."
CONN_ROTATE_ASK = (
    "Пришли новый PAT для <b>{display_name}</b>.\n"
    "GitLab user должен совпадать: <code>{gitlab_username}</code>.\n\n"
    "Сообщение с токеном я удалю сразу после проверки."
)
CONN_ROTATE_OK = "PAT обновлён для <b>{display_name}</b>."
CONN_ROTATE_OWNER_MISMATCH = (
    "Этот PAT принадлежит другому пользователю GitLab — оставил старый."
)
