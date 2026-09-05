ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.5.27 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_PYTHON=/usr/local/bin/python \
    UV_FROZEN=1 \
    UV_NO_CACHE=1

WORKDIR /src

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=.,target=/src \
    uv sync --no-editable --no-dev --frozen

# Top-level modules (app, config, crypto, deps, errors, middlewares, texts) and
# packages (clients, db, handlers, services) are installed into site-packages by
# the sync above; the bind mount is gone after. Copy migration assets so the
# `migrate` service can run `alembic upgrade head` from this image.
COPY alembic.ini ./
COPY migrations ./migrations

CMD ["python", "-m", "app"]
