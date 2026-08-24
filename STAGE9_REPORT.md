# Stage 9 report — Broadcasts

## Scope completed

Stage 9 implements the broadcast module requested by the project specification without replacing
previous functionality.

Implemented:

- durable `Broadcast` and `BroadcastRecipient` models;
- admin list/create/edit/detail screens;
- Telegram HTML text;
- optional JPEG/PNG/WEBP upload;
- URL inline buttons;
- safe browser preview;
- real test send only to the authenticated admin's `telegram_id`;
- immediate launch and UTC scheduling;
- current audience estimate before launch;
- recipient materialization at actual execution time;
- combined segmentation filters;
- Celery background execution;
- Celery beat due-job dispatcher;
- stale worker recovery;
- per-recipient attempts/status/error/message ID;
- stop request for running broadcasts;
- Telegram flood-control handling;
- blocked-user detection and `users.bot_blocked=true` update;
- shared Docker media volume;
- AuditLog actions for create/update/test/schedule/stop.

## Segments

The filter engine supports all required baseline groups and combinations:

- all reachable users;
- active subscription;
- without active subscription;
- active trial;
- trial ended;
- subscription expires exactly in N days;
- subscription expired today/yesterday/within an arbitrary range;
- never paid;
- paid at least once;
- active plan;
- payment provider;
- inactive for N days;
- registration date range.

`bot_blocked=true` and admin-blocked users are excluded from every segment.

## Delivery semantics

The normal configured speed is 25 messages/second and the admin setting is constrained to 1–30.
`TelegramRetryAfter.retry_after` is respected. Network/server errors have bounded retries and a
single-user failure does not abort the broadcast.

Each recipient is persisted as `sending` before the network call. On recovery after a worker crash,
an unresolved `sending` record is deliberately changed to `failed` with a delivery-uncertain error
rather than automatically retried. This is a safety tradeoff: it prevents duplicate mass messages in
the unavoidable gap where Telegram may have accepted a message but PostgreSQL did not yet receive the
success commit.

## Scheduling and recovery

A `beat` container runs the configured Celery beat schedule:

- due broadcasts: every 30 seconds;
- stale running broadcast recovery: every 300 seconds.

A Redis distributed lock prevents duplicate workers from executing the same broadcast concurrently.
The lock is extended during normal progress and Telegram `RetryAfter` waits.

## Media

The API stores uploaded images under `BROADCAST_MEDIA_DIR`. In Docker this is `/data/broadcasts` on a
named `broadcast_media` volume mounted into both `api` and `worker`. The first successful Telegram
photo send can persist the bot-specific `file_id`, allowing subsequent sends to reuse Telegram's file
instead of uploading the local image for every recipient.

## Database

Migration:

```text
20260819_0009_broadcasts.py
```

New tables:

```text
broadcasts
broadcast_recipients
```

New settings:

```text
broadcasts.messages_per_second = 25
broadcasts.max_attempts = 4
```

Alembic head:

```text
20260819_0009
```

SQLAlchemy metadata now contains 22 tables.

## Verification

Performed in the available workspace:

```text
pytest                    72 passed, 3 skipped
python -m compileall      passed
Alembic head              20260819_0009
Alembic 0001 -> 0009 SQL  generated successfully
SQLAlchemy PostgreSQL DDL generated successfully
Jinja templates           22 compiled successfully
Docker Compose YAML       parsed successfully
```

Skipped tests are the existing runtime checks whose optional packages are absent in this execution
environment (`redis`, `aiogram`, `celery`). The packages are present in `pyproject.toml` and installed
inside the project Docker image.

Docker Engine and live Telegram credentials are unavailable in this workspace, so a real
`docker compose up` and a production Telegram broadcast were not claimed as executed.

## Next stage

Stage 10: configurable administrator notifications, subscription-expiration reminders, scheduler,
notification templates, deduplication and delivery logs.
