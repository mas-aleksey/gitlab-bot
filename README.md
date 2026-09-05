# gitlab-bot

A Telegram bot for day-to-day GitLab work: approve and merge MRs, run pipelines,
play manual jobs, cut tags, drive release jobs — without opening the web UI.

Self-hosted, long-polling (no public URL or webhook needed), SQLite for state.
Works against gitlab.com and any self-managed instance.

## What it does

| Command | |
| --- | --- |
| `/pipelines` | run a pipeline on a branch, or drill into manual jobs and play them |
| `/mrs` | list open MRs (yours or the whole scope), approve, unapprove, merge |
| `/tags` | list recent tags, create a new one |
| `/release` | play or retry release jobs in a tag pipeline |
| `/add_connection` | connect a GitLab instance — paste a group/project URL and a PAT |
| `/connections` | switch the default connection, rotate a PAT, delete one |
| `/pool` | who else works in the active project |
| `/invite` | issue an invite code (deep-link) for a teammate |
| `/audit` | 30-day activity in the active project (admin only) |

Several people can share one bot. Each brings their own Personal Access Token, so
GitLab still sees the individual — approvals and merges are attributed correctly,
and permissions are whatever that person already has.

## Security model

- **PATs are encrypted at rest** with Fernet (`FERNET_KEY`); the database holds
  ciphertext, never the token.
- **The message containing a PAT is deleted** from the chat right after validation.
- **Access is invite-only**: `/invite` issues an HMAC-signed code
  (`INVITE_SALT`), and nobody joins the pool without one.
- **Every mutating action is audited** — who did what, to which project, when.

The bot is only as privileged as the tokens people give it. It never asks for
more scope than `api`, and one person's token is never used for another's action.

## Running it

Requires Docker and a bot token from [@BotFather](https://t.me/BotFather).

```bash
cp .env.example .env
# fill in BOT_TOKEN, then generate the two secrets:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FERNET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"                               # INVITE_SALT

docker compose up -d --build migrate gitlab-bot
```

Then message the bot: `/start` → `/add_connection` → paste a GitLab group or
project URL and a PAT with `api` scope.

The first user to register becomes admin. Everyone else needs an invite code.

State lives in `./data/bot.db` (SQLite), mounted into the container at `/data`.

### Without Docker

```bash
uv sync
uv run alembic upgrade head
uv run python -m src.app
```

## Configuration

| Variable | |
| --- | --- |
| `BOT_TOKEN` | from @BotFather |
| `FERNET_KEY` | 32-byte base64 key; encrypts PATs at rest |
| `INVITE_SALT` | ≥32 chars; signs invite codes |
| `DATABASE_URL` | `sqlite+aiosqlite:////data/bot.db` — note the **four** slashes |
| `LOG_LEVEL` | `INFO` by default |

Three slashes in `DATABASE_URL` makes the path relative to the container's working
directory instead of the mounted volume, and the data silently disappears on
restart. Four slashes.

## Development

```bash
uv sync
uv run ruff check .
uv run mypy
uv run pytest
```

GitLab API tests replay recorded HTTP exchanges via
[pytest-recording](https://github.com/kiwicom/pytest-recording), so the suite runs
offline with no instance and no token.

### Re-recording cassettes

Recording talks to a real GitLab, and a recording carries real usernames, emails
and project paths in its response bodies — gzipped and base64-encoded, so they are
invisible to a casual grep. `tests/scrub.py` anonymises them, and `conftest.py`
wires it into recording so a fresh cassette is scrubbed before it ever reaches disk.

```bash
# set PAT, GITLAB_URL, GITLAB_SCOPE_PATH, GITLAB_TEST_PROJECT_PATH in .env first
uv run pytest --record-mode=all
uv run python -m tests.scrub          # scrub existing cassettes (idempotent)
uv run python -m tests.scrub --check  # fail if anything private survived
```

Run `--check` before pushing cassettes anywhere public.

## Architecture

```
src/
├── app.py         entrypoint: long-polling
├── deps.py        composition root — engine, clients, services, routers
├── clients/       GitLab REST v4 client (httpx + backoff)
├── db/            SQLAlchemy models, repositories, session
├── features/      one slice per feature: router + service + texts
└── shared/        keyboards, middlewares, audit helper
```

Feature slices don't import each other, and `shared/` doesn't import features.
Adding a mutating GitLab call means adding a `log_action` next to it, or the
action won't show up in `/audit`.

## License

MIT — see [LICENSE](LICENSE).
