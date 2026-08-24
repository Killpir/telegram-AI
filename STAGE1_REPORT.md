# Stage 1 report

## Scope completed

Stage 1 implements the project foundation: layered package layout, Docker Compose, PostgreSQL, Redis, configuration, core database models, async SQLAlchemy session management, Alembic migrations, FastAPI health endpoints, a Redis-backed Celery worker, Nginx, structured JSON logging and initial tests.

## Architectural decisions

- PostgreSQL is the system of record.
- SQLAlchemy uses the asyncio engine and `async_sessionmaker`.
- Alembic uses an async migration environment; schema creation is performed only by migrations.
- Redis is split logically: application cache/state DB 0, Celery broker DB 1, Celery results DB 2.
- Celery is used for the background worker. Queue-backed business jobs will be added in later stages.
- Secrets are loaded from environment variables; `AppSetting` is not a secret store.
- A separate one-shot `migrate` Compose service runs `alembic upgrade head`; API and worker wait for successful migration completion.
- Telegram bot runtime is intentionally deferred to Stage 2 instead of shipping a fake bot process.

## Core tables in initial migration

- `users`
- `admins`
- `app_settings`
- `audit_logs`
- `error_events`

Domain tables for dialogs/AI usage/subscriptions/payments/referrals/promocodes/broadcasts/notifications will be added by their corresponding stages with separate migrations.

## Verification performed in the build environment

Passed:

- Python `compileall` / AST syntax parsing.
- `pyproject.toml` parsing.
- `docker-compose.yml` YAML parsing and expected service check.
- Alembic offline PostgreSQL SQL generation (`alembic upgrade head --sql`).
- Core settings/model metadata tests: 4 passed.

Not executable in the build environment:

- Real `docker compose up`: Docker Engine is not installed in this environment.
- Full test suite: this environment cannot download missing runtime packages (`asyncpg`, `redis`, `celery`) because package-network access is unavailable. Those dependencies are declared in `pyproject.toml` and are installed by the Docker image in a normal networked build.

## Local/VPS check

```bash
cp .env.example .env
# edit secrets/passwords
docker compose up -d --build
docker compose ps
curl http://localhost/health/live
curl http://localhost/health/ready
```

Then run:

```bash
docker compose run --rm api pytest
```

## Next stage

Stage 2: aiogram bot runtime, `/start`, user registration/update, main menu, profile skeleton, admin notification for first registration, and the `bot` Docker Compose service.

## File tree

```text
.dockerignore
.env.example
.gitignore
.pytest_cache/.gitignore
.pytest_cache/CACHEDIR.TAG
.pytest_cache/README.md
.pytest_cache/v/cache/lastfailed
.pytest_cache/v/cache/nodeids
Dockerfile
Makefile
README.md
alembic.ini
alembic/__pycache__/env.cpython-313.pyc
alembic/env.py
alembic/script.py.mako
alembic/versions/20260818_0001_initial_core.py
alembic/versions/__pycache__/20260818_0001_initial_core.cpython-313.pyc
app/__init__.py
app/__pycache__/__init__.cpython-313.pyc
app/__pycache__/config.cpython-313.pyc
app/__pycache__/logging.cpython-313.pyc
app/admin/__init__.py
app/admin/__pycache__/__init__.cpython-313.pyc
app/ai/__init__.py
app/ai/__pycache__/__init__.cpython-313.pyc
app/api/__init__.py
app/api/__pycache__/__init__.cpython-313.pyc
app/api/__pycache__/health.cpython-313.pyc
app/api/__pycache__/main.cpython-313.pyc
app/api/health.py
app/api/main.py
app/bot/__init__.py
app/bot/__pycache__/__init__.cpython-313.pyc
app/broadcasts/__init__.py
app/broadcasts/__pycache__/__init__.cpython-313.pyc
app/config.py
app/db/__init__.py
app/db/__pycache__/__init__.cpython-313.pyc
app/db/__pycache__/base.cpython-313.pyc
app/db/__pycache__/redis.cpython-313.pyc
app/db/__pycache__/session.cpython-313.pyc
app/db/base.py
app/db/models/__init__.py
app/db/models/admin.py
app/db/models/system.py
app/db/models/user.py
app/db/redis.py
app/db/session.py
app/dialogs/__init__.py
app/dialogs/__pycache__/__init__.cpython-313.pyc
app/logging.py
app/notifications/__init__.py
app/notifications/__pycache__/__init__.cpython-313.pyc
app/payments/__init__.py
app/payments/__pycache__/__init__.cpython-313.pyc
app/plans/__init__.py
app/plans/__pycache__/__init__.cpython-313.pyc
app/promocodes/__init__.py
app/promocodes/__pycache__/__init__.cpython-313.pyc
app/referrals/__init__.py
app/referrals/__pycache__/__init__.cpython-313.pyc
app/repositories/__init__.py
app/repositories/__pycache__/__init__.cpython-313.pyc
app/services/__init__.py
app/services/__pycache__/__init__.cpython-313.pyc
app/subscriptions/__init__.py
app/subscriptions/__pycache__/__init__.cpython-313.pyc
app/users/__init__.py
app/users/__pycache__/__init__.cpython-313.pyc
app/workers/__init__.py
app/workers/__pycache__/__init__.cpython-313.pyc
app/workers/__pycache__/celery_app.cpython-313.pyc
app/workers/__pycache__/tasks.cpython-313.pyc
app/workers/celery_app.py
app/workers/tasks.py
docker-compose.yml
nginx/default.conf
pyproject.toml
scripts/check.sh
tests/__pycache__/test_api.cpython-313.pyc
tests/__pycache__/test_model_metadata.cpython-313-pytest-9.0.2.pyc
tests/__pycache__/test_model_metadata.cpython-313.pyc
tests/__pycache__/test_settings.cpython-313-pytest-9.0.2.pyc
tests/__pycache__/test_settings.cpython-313.pyc
tests/__pycache__/test_worker.cpython-313.pyc
tests/test_api.py
tests/test_model_metadata.py
tests/test_settings.py
tests/test_worker.py
```
