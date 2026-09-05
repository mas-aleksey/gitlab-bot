"""Releases-local UI strings."""

# ---------- project picker ----------

REL_PROJECTS_EMPTY = "В scope подключения нет проектов."
REL_PROJECTS_HEADER = "🚀 Релизы — выбери проект (<code>{scope_path}</code>):"

# ---------- tag-pipelines list ----------

REL_TAG_LIST_HEADER = (
    "🚀 <b>{project_name}</b>\n"
    "Последние релизы (pipelines на тегах):"
)
REL_TAG_LIST_EMPTY = "У проекта пока нет pipelines на тегах."

# ---------- pipeline drill ----------

REL_PIPE_HEADER = (
    "🚀 <b>{project_name}</b>\n"
    "Тег: <code>{ref}</code> — "
    '<a href="{web_url}">pipeline #{id}</a>\n'
    "Downstream: {downstream_count}\n"
)
REL_PIPE_TREE_EMPTY = (
    "⚠ Дерево pipeline пусто — pipeline ещё не создан или недоступен."
)
REL_PIPE_NO_JOBS = "В pipeline пока нет ни одной джобы."
REL_PIPE_LEGEND = "\nЛегенда: ✅ done · 🏃 run · ✋ ready · ❌ fail · ⏭ skip"

# кнопка-заголовок стадии (некликабельная — вешаем noop)
REL_STAGE_HEADER = "── {stage} ──"

# счётчики: ✅3 🏃2 ✋1 ❌0 ⏭0  (компактно, помещается в кнопку)
REL_COUNTS_FMT = "✅{success} 🏃{running} ✋{ready} ❌{failed} ⏭{skipped}"

# ---------- confirm play / retry ----------

REL_CONFIRM_HEADER = (
    "🚀 <b>{project_name}</b> — <code>{ref}</code>\n\n"
    "<code>{name}</code>\n"
    "✋ готово к запуску: <b>{ready}</b> · ❌ упало: <b>{failed}</b>"
)

REL_PLAY_OK = "✅ Запущено: {n}"
REL_PLAY_FAIL = "\n❌ Не удалось: {n}"
REL_PLAY_NONE = "Ни одной джобы в статусе manual — обнови."
REL_RETRY_NONE = "Ни одной упавшей джобы — обнови."

# ---------- buttons ----------

REL_BTN_REFRESH = "🔄 Обновить"

# Префикс к тексту кнопки, когда джоба готова к запуску / перезапуску
REL_BTN_PLAY_PREFIX = "▶ "
REL_BTN_RETRY_PREFIX = "🔁 "

REL_BTN_CONFIRM = "✅ Запустить ✋{n}"
REL_BTN_CONFIRM_RETRY = "🔁 Перезапустить ❌{n}"
REL_BTN_CANCEL = "❌ Отмена"
