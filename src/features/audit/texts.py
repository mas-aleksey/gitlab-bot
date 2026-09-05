"""Audit-local UI strings."""

AUDIT_NOT_ADMIN = "Команда только для админа."
AUDIT_EMPTY = "За последние {days} дней действий не было."
AUDIT_NO_SUCH_USER = (
    "Нет такого в пуле: {ref}\n"
    "Список — /pool, потом /audit @username или /audit &lt;tg_user_id&gt;."
)
AUDIT_EMPTY_ACTOR = "У {name} за последние {days} дней действий не было."
AUDIT_HEADER = "🕵️ <b>Аудит за {days} дней</b> — всего действий: {total}\n"
AUDIT_HEADER_SCOPE = (
    "🕵️ <b>Аудит за {days} дней</b> — <code>{scope_path}</code>, действий: {total}\n"
)
AUDIT_HINT = "\n\nПо одному человеку — /audit @username, все проекты — /audit all"
AUDIT_ALL_HINT = "\n\nТолько по активному проекту — /audit"
AUDIT_NO_CONNECTION = (
    "Нет активного подключения — не от чего считать scope.\n"
    "/add_connection чтобы подключиться, или /audit all — по всем проектам."
)
AUDIT_EMPTY_SCOPE = (
    "В <code>{scope_path}</code> за последние {days} дней действий не было."
)
AUDIT_ACTOR = "\n<b>{name}</b> — {total}"
AUDIT_ACTOR_ACTION = "\n  • {action} — {n}"
AUDIT_RECENT_HEADER = "\n\n<b>Последние события:</b>"
AUDIT_RECENT_ROW = "\n{when} {name} <code>{action}</code> {target}"
