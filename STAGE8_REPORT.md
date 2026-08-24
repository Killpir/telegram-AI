# Stage 8 report — Search, user analytics and manual administration

## Scope completed

Stage 8 implements the advanced admin workflow required after the base admin panel:

- combined user search and filters;
- full user card and histories;
- per-user economics;
- manual subscription/trial/user operations;
- direct admin-to-user Telegram messages with durable delivery history;
- arbitrary-period Dashboard analytics and charts.

## User search

`GET /admin/users` supports combined filters for:

- internal user ID;
- Telegram ID;
- username;
- first/last name;
- active subscription / no active subscription;
- active trial / ended trial / ended subscription;
- never paid / paid at least once;
- active plan;
- payment provider;
- registration date range;
- active within N days;
- bot blocked status;
- administrator blocked status.

Search conditions are expressed with correlated `EXISTS` queries rather than large multiplying joins, while the result page joins only compact aggregate subqueries needed for display.

## User card

`GET /admin/users/{id}` shows:

- Telegram identity and registration metadata;
- referrer;
- current subscription or trial;
- request and token quota usage;
- payment history;
- revenue grouped by currency;
- OpenAI usage, tokens and cost;
- subscription/trial history;
- referral rewards;
- promo activations;
- admin direct-message history;
- dialog/message counts.

Raw dialog text is not visible by default. It is shown only when `privacy.allow_admin_dialog_access=true` is explicitly enabled in service settings.

## Manual actions

All actions are CSRF-protected and written to `AuditLog`:

- grant/extend a plan period;
- extend an active subscription by arbitrary N days;
- add request quota;
- change active plan;
- cancel active subscription;
- block/unblock user AI access;
- reset trial;
- allow a new trial;
- send a direct Telegram message.

Dangerous actions have explicit browser confirmation in the UI. Database row locks are used before access mutations.

## Direct-message durability

Migration `20260819_0008` adds `admin_direct_messages` with statuses:

- `pending`;
- `sent`;
- `failed`.

The pending attempt is committed before calling the Telegram Bot API. This means a process crash after the external side effect cannot make the attempt disappear completely. Telegram `403/blocked` responses also mark `users.bot_blocked=true`.

## Dashboard and economics

The Dashboard accepts an arbitrary inclusive date range up to 367 days and provides daily charts for:

- registrations;
- purchases;
- RUB revenue;
- OpenAI cost in USD;
- gross profit in RUB when an FX rate is configured;
- subscription-period coverage.

It also reports:

- payment count and paying users;
- revenue grouped by currency;
- revenue grouped by provider and currency;
- estimated/actual RUB payment fees;
- input/output token totals;
- ARPU and ARPPU for RUB revenue;
- average OpenAI cost per AI user;
- trial-to-paid conversion.

### Currency rule

RUB, XTR and crypto amounts are never summed into a single revenue number. Gross profit in RUB is:

`RUB revenue - RUB payment fees - OpenAI USD cost * economics.usd_to_rub`

When `economics.usd_to_rub=0`, gross profit is deliberately shown as unavailable instead of inventing an exchange rate.

### Historical subscription graph limitation

The existing schema did not previously retain a full event history for every old subscription cancellation/status transition. Therefore the historical graph is explicitly labelled **subscription-period coverage**: it counts subscription intervals overlapping a day and is not presented as an exact past status snapshot.

## Migration

New Alembic revision:

`20260819_0008_user_admin_analytics.py`

It creates `admin_direct_messages` and seeds:

- `economics.usd_to_rub = 0`;
- `privacy.allow_admin_dialog_access = false`.

## Verification

- regression tests from stages 1–7 pass;
- Stage 8 metadata/filter/router tests pass;
- Python `compileall` passes;
- all Jinja templates compile through the existing template test;
- Alembic has one head: `20260819_0008`;
- full offline PostgreSQL upgrade from `0001` through `0008` generates successfully;
- SQLAlchemy metadata contains 20 tables including `admin_direct_messages`;
- `pyproject.toml` and `docker-compose.yml` parse successfully.

Docker Engine is not available in the execution environment, so a real `docker compose up` is not claimed as tested here.
