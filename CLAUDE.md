# gitlab-bot

Telegram-бот для операций над GitLab (MR, pipelines, tags, releases). Long-polling, aiogram 3.x, SQLAlchemy async + SQLite, httpx для GitLab REST v4.

## Стек

- **Python 3.14**, менеджер зависимостей — `uv` (см. `uv.lock`).
- **aiogram 3.x** — long-polling, `MemoryStorage` для FSM (см. TODO в `plan.md`, п. 3).
- **SQLAlchemy 2.x async** + **aiosqlite**, миграции — **Alembic**.
- **httpx[http2]** + `backoff` для GitLab REST v4.
- **cryptography.Fernet** — шифрование PAT в БД.
- **pydantic-settings** — конфиг из `.env`.
- **loguru** — логи.
- Линт: **ruff** (line-length 100), тайпчек: **mypy strict**, тесты: **pytest** + `pytest-recording` (VCR).

## Структура

```
src/
├── app.py              # entrypoint: long-polling
├── config.py           # Settings (pydantic-settings)
├── deps.py             # composition root: engine, sessionmaker, gitlab, все сервисы, роутеры
├── crypto.py           # PatCrypto (Fernet)
├── errors.py           # регистрация обработчиков ошибок в Dispatcher
├── clients/
│   ├── base.py         # base HTTP-клиент (httpx + backoff)
│   └── gitlab/         # GitLab REST v4 клиент + схемы (pydantic)
├── db/                 # SQLAlchemy модели, session, репозитории
├── features/           # feature-slices: каждый со своим router + service + texts
│   ├── audit/          # /audit [@user] — сводка действий за 30 дней (только админ)
│   ├── onboarding/     # invite-коды, регистрация в pool, /pool по scope
│   ├── connections/    # подключения к GitLab (URL + PAT), шифрование
│   ├── pipelines/      # список pipelines, drill-in, play manual jobs
│   ├── mrs/            # список MR (my/scope), карточка, approve/merge (в работе)
│   ├── tags/           # список тегов, создание (в работе)
│   ├── releases/       # управление release-jobs в tag-pipelines
│   └── help/           # /help — статичная справка
└── shared/             # тексты, клавиатуры, edit_or_answer, middlewares, audit-запись
```

**Правило**: features/* не импортят друг друга, shared/* не импортит features/deps/errors. Пока on-the-honor-system — планируется import-linter.

**Меню команд** (автокомплит по «/» в клиенте) — `features/help/texts.py::MENU_COMMANDS`,
выставляется в `app.py` через `set_my_commands` при старте. Новая команда — строка
и туда, и в `HELP_USER`, иначе разъедется.

**Pool по scope**: `/pool` показывает не весь `users`, а тех, чьё подключение
на том же инстансе пересекается по scope с активным подключением вызывающего —
пути сравниваются по границе сегмента (`a/b` видит `a/b/c` и `a`, но не `a/bc`),
запрос — `repos.list_pool_in_scope`. Весь pool — `/pool all`. Без активного
подключения scope неизвестен, список не строится.

**Аудит**: каждое мутирующее действие пишется в `audit_events` через
`shared.audit.log_action(sessionmaker, actor_tg_id=..., action=..., target=...)` —
хендлер зовёт её после успеха (ошибка записи не роняет действие). Слаги действий:
`mr.approve/unapprove/merge/proxy_approve/proxy_unapprove`, `tag.create`,
`pipeline.run`, `release.play/retry`, `job.play`,
`connection.add/delete/rotate_pat`, `invite.issue`, `user.register`.
Список должен покрывать все мутирующие методы `GitLabClient` (`create_pipeline`,
`play_job`, `approve`, `unapprove`, `merge`, `create_tag`) — добавляешь новую
мутацию, добавляй и `log_action`, иначе действие в `/audit` не попадёт. У события есть свой scope — колонки `base_url` +
`project_path` (`path_with_namespace` проекта; для `connection.*` — scope-путь
подключения; NULL у действий вне проекта: `invite.issue`, `user.register`).

Читает — `/audit` (только `is_admin`): счётчики по типам за 30 дней на каждого
юзера + последние 10 событий. Без аргумента — **события активного проекта**
(scope подключения и всё вложенное, сравнение по границе сегмента, как в
`/pool`), `/audit @username` — то же по одному человеку, `/audit all` — вообще
всё, включая другие проекты и события без проекта.

## Разработка

```bash
# зависимости (создаст .venv, синхронизирует по uv.lock)
uv sync

# запуск бота локально (нужен .env)
uv run python -m src.app

# миграции
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "название"

# линт + тайпчек + тесты
uv run ruff check .
uv run mypy
uv run pytest
```

## Конфиг

Все секреты — в `.env` (не коммитить). Шаблон — `.env.example`.

Обязательные:
- `BOT_TOKEN` — от @BotFather.
- `FERNET_KEY` — 32 байта base64, генерация: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- `INVITE_SALT` — минимум 32 символа, для HMAC инвайтов.
- `DATABASE_URL` — для деплоя всегда `sqlite+aiosqlite:////data/bot.db` (4 слэша = абсолютный путь).

Опциональные (только для тестов с реальным GitLab):
- `PAT`, `GITLAB_URL`, `GITLAB_SCOPE_PATH`, `GITLAB_TEST_PROJECT_PATH`.

## Деплой

Разворачивается как самостоятельный compose-проект или через `include` из родительского compose:

```bash
docker compose up -d --build migrate gitlab-bot
# или пересобрать только этот сервис:
docker compose build migrate gitlab-bot && docker compose up -d migrate gitlab-bot
```

Контейнеры: `gitlab-bot-migrate` (one-shot, Alembic upgrade) → `gitlab-bot` (long-polling).
Данные — `./data/bot.db` (SQLite), монтируется в `/data` внутри контейнера.

**Сеть**: дефолтный bridge (не `proxy`), т.к. входящего HTTP нет — только исходящие вызовы Telegram и GitLab.

## Грабли

- `DATABASE_URL` — обязательно **4 слэша** после `sqlite+aiosqlite:` (абсолютный `/data/bot.db`). С тремя слэшами SQLAlchemy трактует путь как относительный к CWD контейнера (`/src/`), а volume монтируется в `/data` — данные потеряются при рестарте.
- `MemoryStorage` для FSM теряет состояние при рестарте. Пользователь при вводе URL/PAT после `docker compose up` получает «непонятно что происходит». Переход на `SqliteStorage` — в `plan.md` п. 3.
- Dockerfile использует `uv sync --no-editable` + `force-include` в `pyproject.toml` для top-level модулей (`app.py`, `config.py`, `crypto.py`, `deps.py`, `errors.py`). Пакеты (`clients`, `db`, `features`, `shared`) — через `[tool.hatch.build.targets.wheel] packages`. Bind-mount `--mount=type=bind,source=.,target=/src` пропадает после `uv sync` — оставшиеся файлы копируются отдельно (`alembic.ini`, `migrations/`).
- `pytest-recording` VCR-кассеты — не переписывать без явного `--record-mode=all`. Для записи новых нужны `PAT` + `GITLAB_URL` в `.env`.

## Планы

См. `plan.md` (feature-бэклог, техдолг, ops).
