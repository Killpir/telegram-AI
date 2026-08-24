# Telegram Admin Migration Report

## Goal

Replace the browser-first administrative workflow with a Telegram-native administration interface while preserving FastAPI/Nginx for health checks, external payment callbacks and provider checkout flows.

## Access model

- `ADMIN_TELEGRAM_IDS` is the authority for administrator identity.
- `⚙️ Админ-панель` appears only for configured IDs.
- `/admin`, admin FSM inputs and every `adm:*` callback re-check the sender ID.
- Multiple admins are supported with comma-separated IDs.
- Removing an ID from `.env` and restarting the bot removes its admin access.
- Admin notification recipients are constrained to the same ENV allow-list.

## Telegram sections implemented

- Dashboard statistics.
- User search and detailed cards.
- User block/unblock, trial reset, tariff grant, add days/requests, direct message.
- Plan creation/editing/enable/recommendation/safe deletion.
- Trial settings.
- AI runtime settings and model pricing.
- Recent payments.
- Payment provider ON/OFF, test mode and fee assumptions.
- Promo creation/toggle.
- Referral settings/statistics.
- Broadcast creation from text or photo+caption, audience selection, URL buttons, immediate start, UTC scheduling and stop.
- Admin notification categories.
- Subscription reminder timing and message templates.
- Service settings and maintenance mode.
- Errors and resolve action.
- Audit history.

## Browser admin

`WEB_ADMIN_ENABLED=false` is the new default. When false, the FastAPI/Jinja `/admin/*` routes and admin static files are not mounted. The API process remains required for:

- `/health/live`;
- `/health/ready`;
- YooMoney/YooKassa/Platega/Crypto Pay callbacks;
- external provider checkout flows.

The historical browser interface can still be explicitly enabled for compatibility with `WEB_ADMIN_ENABLED=true`.

## Production preflight

The normal production preflight now requires a non-empty `ADMIN_TELEGRAM_IDS` instead of an active browser superadmin. A DB superadmin is required only when `WEB_ADMIN_ENABLED=true`.

## Database

No new migration was necessary. The Telegram panel reuses the existing domain tables, `AppSetting`, notification tables and `AuditLog`. Alembic head remains `20260819_0010`.

## Verification

- Python compileall: passed.
- 91 tests passed; 3 runtime-only tests for unavailable local `redis`, `aiogram`, `celery` were skipped in this build environment.
- Alembic head: `20260819_0010`.
- Full offline PostgreSQL migration chain: passed.
- TOML/YAML parsing: passed.
- Shell syntax for smoke script: passed.
- Literal Telegram callback payloads checked to remain within Bot API callback-data size limits.

Live Telegram behavior and Docker Compose boot still require a Docker-capable target host with real bot credentials.
