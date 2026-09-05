"""Tags-local UI strings: project picker, tag list, create flow."""

TAGS_PROJECTS_EMPTY = "В scope подключения нет проектов."
TAGS_PROJECTS_HEADER = "Выбери проект (<code>{scope_path}</code>):"

TAGS_MENU_ASK = "Что делаем?"

TAGS_REF_ASK = "На какой ref навесить тег?"
TAGS_REF_TYPE_IN = (
    "Пришли ref одним сообщением: имя ветки, тега или SHA "
    "(например <code>main</code> или <code>a1b2c3d</code>)."
)
TAGS_REF_NOT_FOUND = "❌ Ref не найден в проекте."

TAGS_NAME_ASK = (
    "Ref: <code>{ref}</code>\n\n"
    "Пришли имя нового тега (например <code>v1.2.3</code>).\n"
    "Последние теги:\n{recent}"
)
TAGS_NAME_RECENT_ROW = "• <code>{name}</code>"
TAGS_NAME_RECENT_EMPTY = "—"
TAGS_NAME_INVALID = "❌ Пустое имя тега."
TAGS_NAME_TAKEN = "❌ Тег с таким именем уже существует."

TAGS_CREATED = (
    '✅ Тег <b>{name}</b> создан на <code>{commit_sha}</code>.\n'
    '<a href="{web_url}">Открыть в GitLab</a>'
)

# ---------- buttons ----------

TAGS_BTN_CREATE = "➕ Создать тег"
TAGS_BTN_TYPE_REF = "✍️ Ввести ref"
