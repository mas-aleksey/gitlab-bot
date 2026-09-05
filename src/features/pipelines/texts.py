"""Pipelines-local UI strings: project picker, mode, jobs, buckets."""

PIPE_PROJECTS_EMPTY = "В scope подключения нет проектов."
PIPE_PROJECTS_HEADER = "Выбери проект (<code>{scope_path}</code>):"
PIPE_PROJECT_HEADER = "<b>{project_name}</b>\n<code>{path_with_namespace}</code>"
PIPE_MODE_ASK = "Что делаем?"
PIPE_BRANCH_ASK = "Выбери ветку для запуска pipeline:"
PIPE_BRANCH_TYPE_IN = (
    "Пришли имя ветки одним сообщением (например <code>feature/xyz</code>)."
)
PIPE_BRANCH_NOT_FOUND = "❌ Ветка не найдена в проекте."
PIPE_RECENT_ASK = "Последние pipelines — выбери, чтобы посмотреть manual/trigger джобы:"
PIPE_RECENT_EMPTY = "У проекта пока нет ни одного pipeline."
PIPE_JOBS_ASK = "Отметь джобы, которые нужно запустить, и жми «▶️ Запустить»."
PIPE_JOBS_NONE = "В этом pipeline нет джоб, которые можно запустить руками."
PIPE_JOBS_NOTHING_SELECTED = "Ничего не выбрано."
PIPE_RUN_OK = (
    "✅ Pipeline <b>#{id}</b> на ветке <code>{ref}</code> запущен.\n"
    '<a href="{web_url}">Открыть в GitLab</a>'
)
PIPE_BTN_RUN_MODE = "🚀 Запустить на ветке"
PIPE_BTN_RECENT_MODE = "📋 Последние pipelines"
PIPE_BTN_TYPE_BRANCH = "✍️ Ввести имя ветки"
PIPE_BTN_PLAY_SELECTED = "▶️ Запустить выбранные"
PIPE_BTN_REFRESH = "🔄 Обновить"
PIPE_BTN_REFRESH_JOBS = "🔄 Обновить статусы"
PIPE_BTN_BACK_TO_RECENT = "К списку pipelines"
PIPE_BTN_BACK_TO_ROOT = "← К pipeline"
PIPE_BTN_BUCKET = "📦 {label}"
PIPE_BUCKET_HEADER = "Downstream: <b>{label}</b>"
PIPE_JOB_SELECTED = "✅"
PIPE_JOB_UNSELECTED = "⬜"
