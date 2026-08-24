# Stage 10 report — Notifications and subscription lifecycle scheduler

## Scope completed

Stage 10 implements the administrator notification center and subscription-expiration scheduler from
the project specification without replacing earlier payment, trial, broadcast or AI functionality.

Implemented:

- durable administrator-recipient settings;
- independent event ON/OFF flags per admin recipient;
- backward-compatible `ADMIN_TELEGRAM_IDS` fallback while the DB recipient table is empty;
- new-user notification;
- trial-activation notification;
- successful-purchase notification;
- failed-payment notification;
- aggregated OpenAI/payment/critical application error alerts;
- secret redaction before error persistence/Telegram delivery;
- subscription reminders before expiration, on the expiry date, at/after exact expiration and after
  configurable post-expiry day offsets;
- editable notification templates;
- durable notification delivery log;
- unique deduplication keys;
- stale pending-notification recovery without blind resend;
- Celery Beat scheduler;
- admin web UI under `/admin/notifications`;
- AuditLog entries for notification settings and recipient changes.

## Database changes

Migration:

```text
20260819_0010_notifications.py
```

New models/tables:

```text
AdminNotificationSetting -> admin_notification_settings
NotificationLog           -> notification_logs
```

`ErrorEvent` additionally stores:

```text
last_notified_at
notification_count
```

and `fingerprint` is now uniquely constrained so repeated occurrences can be aggregated safely.

`NotificationLog` stores the logical event kind, unique dedupe key, recipient, related user/payment/
subscription/error event, delivery state, attempts, scheduled/reserved/sent timestamps, Telegram
message ID, safe payload and error text.

Current Alembic head:

```text
20260819_0010
```

SQLAlchemy metadata contains 24 tables.

## Administrator notification settings

The page:

```text
/admin/notifications
```

allows an administrator to manage recipients and independently toggle:

```text
new user
trial activation
successful purchase
failed payment
OpenAI error
payment-provider error
critical application error
```

If no `admin_notification_settings` rows exist, configured `ADMIN_TELEGRAM_IDS` are used. Once a DB
recipient exists, the DB list is authoritative so individual event delivery can be managed entirely
from the admin panel.

Business events are committed before their Telegram notification side effect. In particular, a
successful payment is reported only from the committed `paid` settlement result; a repeated
idempotent webhook does not generate a second purchase alert.

A post-settlement Telegram notification failure is isolated from the provider webhook response. The
already committed payment remains successful and the webhook still receives a successful response;
the secondary notification failure is reported independently instead of provoking provider retries.

## Error aggregation

Errors are fingerprinted from sanitized service/category/type/message data. Repeated matching events
increment `occurrence_count` instead of inserting an unbounded stream of duplicate rows.

The setting:

```text
notifications.errors.cooldown_minutes = 30
```

controls when an existing fingerprint is eligible to alert administrators again. The count still
increments while alert delivery is cooling down.

Sensitive values are redacted from exception text/context, including common API token patterns and
keys containing token/secret/password/cookie/API-key/card semantics. Telegram alerts intentionally do
not include traceback dumps or secrets.

Integration points include:

- OpenAI chat failures -> `openai_error`;
- payment/provider failures -> `payment_error`;
- unhandled aiogram update failures -> `critical_error`;
- unhandled FastAPI request failures -> `critical_error`;
- broadcast worker execution failures -> `critical_error`.

## Subscription reminder configuration

Seeded defaults:

```text
notifications.subscription.enabled = true
notifications.subscription.days_before = [3, 2, 1]
notifications.subscription.expiry_day = true
notifications.subscription.at_expiry = true
notifications.subscription.days_after = [1]
```

Templates are editable in the admin UI and accept only the validated placeholders:

```text
{plan_name}
{days}
{expires_date}
{expires_datetime}
```

The default flow covers:

```text
3 days before
2 days before
1 day before
calendar expiry day
actual expiration
1 day after expiration (if still not renewed)
```

The day lists support custom positive values rather than hardcoding only 3/2/1 and +1.

The project has no per-user timezone field yet; scheduler comparisons and stored timestamps therefore
use the project's UTC-normalized timestamps. This is an explicit current-system behavior rather than
an invented user-local timezone.

## Race and deduplication safety

Every outbound notification is reserved in PostgreSQL with a unique `dedupe_key` and committed before
Telegram is called. Concurrent schedulers therefore cannot reserve the same logical reminder twice.

For a subscription reminder the key includes the current `expires_at` value. A renewal extends the
same subscription row, so a later paid period naturally receives a different expiry-version key and
can receive its own 3/2/1 reminder series.

The scanner obtains candidate rows, then re-reads and locks each subscription using `SELECT ... FOR
UPDATE` before the final due-event decision. This prevents a stale candidate loaded before a payment
renewal from incorrectly sending an expiration notice after that renewal committed.

Expiry-day priority prevents a late `1 day before` reminder from being sent alongside the explicit
`today is the last day` event. After expiration, reminders are skipped when another current paid
subscription already provides access.

`NotificationLog.dedupe_key` protects known logical duplicates. As with any external side effect,
there is an unavoidable crash window after Telegram may accept a message but before the local success
commit. Stale `pending` logs are therefore marked `failed` with `delivery uncertain` instead of being
blindly retried and risking a duplicate notification.

## Celery scheduler

Celery Beat now contains:

```text
notifications.subscription_scan  every 60 seconds
notifications.recover_stale       every 900 seconds
```

The scan frequency lets the system support the configured exact-expiration event without waiting for
a coarse daily job. Existing broadcast Beat jobs continue unchanged.

## Admin UI

The notification page provides:

- delivery counts (`sent`, `failed`, `blocked`, `pending`);
- recipient add/update/delete controls;
- per-recipient event toggles;
- subscription lifecycle toggles/day lists;
- editable HTML templates;
- error aggregation cooldown;
- recent delivery-log inspection.

All modifying forms use the existing admin CSRF protection and produce AuditLog entries.

## Main files added/changed

```text
app/db/models/notification.py
app/db/models/system.py
app/notifications/repository.py
app/notifications/config.py
app/notifications/sanitize.py
app/notifications/errors.py
app/notifications/admin.py
app/notifications/service.py
app/notifications/admin_router.py
app/admin/templates/admin/notifications.html
app/bot/middlewares/errors.py
app/bot/handlers/start.py
app/bot/handlers/subscription.py
app/bot/handlers/chat.py
app/bot/handlers/payments.py
app/api/main.py
app/api/payments.py
app/broadcasts/sender.py
app/workers/tasks.py
app/workers/celery_app.py
alembic/versions/20260819_0010_notifications.py
tests/test_stage10_notifications.py
```

## Verification

Performed in the available execution workspace:

```text
pytest                    80 passed, 3 skipped
python -m compileall      passed
Alembic head              20260819_0010
Alembic 0001 -> 0010 SQL  generated successfully
SQLAlchemy PostgreSQL DDL generated successfully
SQLAlchemy tables         24
Jinja templates           23 compiled through project environment
Docker Compose YAML       parsed successfully
pyproject.toml            parsed successfully
```

The three skipped tests are the existing runtime checks whose packages are absent from this workspace
(`redis`, `aiogram`, `celery`). They remain project dependencies installed by the Docker image.

Docker Engine, a real Telegram bot token, live OpenAI credentials and payment-provider credentials are
not available in this workspace. Therefore a real `docker compose up`, live scheduled Telegram send,
and live provider webhook cycle are deliberately not claimed as executed here.

## Next stage

Stage 11: expanded tests, security review, error-handling review, complete README/production runbook,
production deployment checks and the final end-to-end scenario audit.
