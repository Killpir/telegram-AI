# Telegram AI SaaS — Final (Stage 11)

Production-oriented Telegram AI SaaS project built incrementally from the project specification.

## Implemented

### Stage 1 — infrastructure

- Python 3.12 layered application structure.
- FastAPI liveness/readiness API.
- Async SQLAlchemy 2 + PostgreSQL.
- Redis and Celery worker.
- Alembic migrations.
- Structured JSON logging.
- Nginx and Docker Compose.

### Stage 2 — Telegram users

- Real `aiogram 3` bot service using long polling.
- Race-safe `/start` registration.
- User profile refresh without duplicate users.
- Registration source/start parameter storage.
- Main menu, `/profile`, `/help`.
- One-time admin notification for a new user.

### Stage 3 — AI chat

- OpenAI Responses API through async `httpx`.
- Dialog/message persistence in PostgreSQL.
- `/new` and exactly one active dialog per user.
- Per-user Redis conversation lease.
- Controlled context and automatic old-history summarization.
- `AIUsage` with input/cached/output/reasoning tokens, duration, status and API cost.
- Database-backed `AIModelPricing`.
- Global anti-abuse request/token limits.
- Safe handling of long Telegram responses.

### Stage 4 — plans, trial and subscriptions

- Database-managed `Plan` model. No tariff price or quota is hardcoded in Telegram handlers.
- Seed plans `Lite`, `Plus`, `Max`; they are normal DB rows and can be changed later from the admin UI.
- Separate RUB, Stars and USD price fields. Stars/USD seed values remain `NULL` until payment providers are configured.
- Plan fields include duration, ordinary/smart request limits, internal input/output token limits, max output tokens, feature flags, display order and recommended/active flags.
- Separate `Trial` history entity plus `users.trial_used` one-time eligibility flag.
- Trial settings are stored in `AppSetting`:
  - enabled;
  - duration;
  - ordinary/smart request limits;
  - internal input/output token limits;
  - automatic activation;
  - admin notification toggle.
- Manual trial activation from the subscription screen.
- Optional automatic trial activation from `/start`.
- Trial cannot be activated twice unless a later admin action explicitly resets `users.trial_used`.
- Trial cannot be consumed while a paid subscription is already active.
- Admin Telegram notification after a successful trial activation when enabled.
- Separate `Subscription` entity with `active/expired/cancelled/blocked` statuses.
- Exactly one active paid subscription per user enforced with a PostgreSQL partial unique index.
- Early renewal follows:

  ```text
  new_expires_at = max(now, current_expires_at) + plan_duration
  ```

- Early renewal also adds the purchased plan quota to the current subscription allowance, so buying another 30-day period adds both time and usage entitlement.
- Paid activation cancels an unfinished active trial without restoring trial eligibility.
- Lazy expiration repair: profile/chat access marks stale active trial/subscription rows `expired` when their end time is already past. A scheduler will later make expiration proactive.
- Access resolution always prefers paid subscription over trial.
- AI requests now require real access; registered users without trial/subscription are refused before OpenAI spend.
- Successful chat usage increments the correct trial/subscription request and token counters.
- Summarization token usage is also charged to the current access token budget, but does not consume a user-visible request.
- Plan `max_output_tokens` additionally caps the global AI output-token setting.
- `/subscription` and `👑 Подписка` show current access and available DB plans.
- `/profile` shows the actual tariff/trial, expiration date and request usage.


## Telegram-native administration (current)

The primary admin UI is now inside the Telegram bot. Set:

```dotenv
ADMIN_TELEGRAM_IDS=123456789
WEB_ADMIN_ENABLED=false
```

Authorized IDs receive a `⚙️ Админ-панель` reply-keyboard button and can also use `/admin`. Every admin callback re-checks `ADMIN_TELEGRAM_IDS`. The historical FastAPI/Jinja browser admin is disabled by default but can be re-enabled explicitly with `WEB_ADMIN_ENABLED=true`; health checks and payment webhooks remain served regardless.

See [`TELEGRAM_ADMIN.md`](TELEGRAM_ADMIN.md) for the complete Telegram administration workflow.

## Requirements and credentials

Development/local requirements:

- Python 3.12+ for direct test/tool execution;
- Docker Engine + Docker Compose plugin for the supported application stack;
- a Telegram bot token created through `@BotFather`;
- an OpenAI API key created in the OpenAI Platform project used for this service;
- provider merchant credentials only for payment systems you intend to enable.

For a real VPS deployment, HTTPS, backup, restore, update, smoke-test and incident procedures are
documented in [`PRODUCTION_RUNBOOK.md`](PRODUCTION_RUNBOOK.md). The final security assumptions and
known residual risks are in [`SECURITY_REVIEW.md`](SECURITY_REVIEW.md).

Telegram token setup:

1. Open `@BotFather` in Telegram.
2. Create/select the bot and copy its Bot API token.
3. Put it only in `.env` as `BOT_TOKEN`; never paste it into an admin chat or message.
4. Configure bot username/support/terms as needed.

OpenAI key setup:

1. Create/select the OpenAI API project that will own this service usage.
2. Create an API key for the deployment.
3. Put it only in `.env` as `OPENAI_API_KEY`.
4. Verify the active model and per-token prices in **⚙️ Админ-панель → 🤖 AI** before enabling paid access.

## Start

Copy configuration:

```bash
cp .env.example .env
```

Set at minimum:

```dotenv
BOT_TOKEN=123456:telegram-bot-token
OPENAI_API_KEY=sk-...
ADMIN_TELEGRAM_IDS=123456789
POSTGRES_PASSWORD=strong-password
DATABASE_URL=postgresql+asyncpg://ai_bot:strong-password@postgres:5432/ai_bot
SECRET_KEY=a-long-random-secret-at-least-32-characters
WEBHOOK_SECRET=a-long-random-webhook-secret
```

Then:

```bash
docker compose up -d --build
```

Check:

```bash
docker compose ps
curl http://localhost/health/live
curl http://localhost/health/ready
docker compose logs -f bot
```

## Telegram commands

```text
/start         registration and main menu
/new           start a new independent dialog
/profile       current access and usage
/subscription  plans and trial activation
/referral      personal referral link and rewards
/promo CODE    activate a promo code
/help          usage help
```

Normal text is treated as an AI request only when the user has active trial/paid access.

## Access flow

```text
Telegram text
  ↓
existing User check
  ↓
runtime AI config
  ↓
per-user Redis conversation lease
  ↓
resolve paid Subscription
  ↓ if none
resolve active Trial
  ↓
check access request/token quotas
  ↓
model pricing preflight
  ↓
global anti-abuse limits
  ↓
active Dialog
  ↓
optional summarization
  ↓
charge summary tokens to current access
  ↓
OpenAI Responses API
  ↓
Message + AIUsage + API cost
  ↓
charge 1 request + chat tokens to current access
  ↓
commit while conversation lease is held
  ↓
Telegram response
```

A user with no active entitlement is stopped before the OpenAI API call and before consuming the global request-rate budget.

## Plans

Initial seed data:

| Plan | RUB | Days | Requests | Smart requests |
|---|---:|---:|---:|---:|
| Lite | 199 ₽ | 30 | 300 | 0 |
| Plus | 349 ₽ | 30 | 1000 | 20 |
| Max | 599 ₽ | 30 | 2000 | 75 |

These values are seed data only. Runtime code reads plans from PostgreSQL.

`price_stars` and `price_usd` are separate nullable fields because provider-specific prices should be configured before Stage 5 exposes payment buttons.

## Trial settings

Stage 4 seeds these `AppSetting` keys:

```text
trial.enabled
trial.duration_days
trial.requests_limit
trial.smart_requests_limit
trial.input_tokens_limit
trial.output_tokens_limit
trial.auto_activate
notifications.admin.trial_activation_enabled
```

Default values are 3 days, 20 ordinary requests, no smart requests, manual activation, and admin notification enabled.

Existing trial rows snapshot their limits at activation. Changing the settings affects future trial activations, not historical rows.

## Subscription entitlement accounting

A `Subscription` stores both usage and accumulated entitlement:

```text
requests_limit / requests_used
smart_requests_limit / smart_requests_used
input_tokens_limit / input_tokens_used
output_tokens_limit / output_tokens_used
```

On an early renewal, expiration is extended from the later of `now` and the current expiration date, and the newly purchased plan's quotas are added to the existing entitlement. Existing usage is not reset.

This avoids the common bug where a user pays for another 30 days but receives no additional request allowance.

## Runtime AI settings

The Stage 3 `AppSetting`/environment configuration remains active:

```text
ai.primary_model
ai.summary_model
ai.system_prompt
ai.reasoning_effort
ai.temperature
ai.max_output_tokens
ai.max_input_chars
ai.history_messages
ai.summary_trigger_messages
ai.context_max_chars
ai.request_timeout_seconds
ai.requests_per_minute
ai.requests_per_day
ai.requests_per_month
ai.monthly_input_tokens
ai.monthly_output_tokens
```

Plan output limits are applied on top of `ai.max_output_tokens`; the lower value wins.

## Database

Stage 4 adds:

```text
plans
trials
subscriptions
users.trial_used
```

Important constraints/indexes:

- `plans.code` unique;
- active-plan/sort index;
- FK `trials.user_id -> users.id`;
- one active trial per user partial unique index;
- trial expiration/status indexes;
- FK `subscriptions.user_id -> users.id`;
- FK `subscriptions.plan_id -> plans.id` with `RESTRICT` delete;
- one active subscription per user partial unique index;
- subscription user/status, plan/status and expiration indexes;
- status/date/quota check constraints.

The future payment foreign key is intentionally added in Stage 5 together with the real `Payment` entity rather than creating a dangling relationship before that table exists.

## Alembic

Current revisions:

```text
20260818_0001  initial core
20260818_0002  user registration source
20260818_0003  AI chat, dialogs, messages, usage and pricing
20260818_0004  plans, trials and subscriptions
```

Apply migrations:

```bash
docker compose run --rm migrate
```

Inspect offline SQL:

```bash
alembic upgrade head --sql
```

Do not replace migrations with `Base.metadata.create_all()` in production.

## Tests

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest -ra
```

Stage 4 tests include:

- one-time trial activation;
- trial limit snapshotting;
- trial refusal during active paid access;
- paid access precedence over trial;
- fallback from an expired subscription to active trial;
- request quota exhaustion;
- renewal from current expiration rather than from `now`;
- renewal of an already expired subscription from `now`;
- accumulation of request/smart/token entitlements on early renewal;
- all Stage 1–3 tests.

## Services

```text
postgres
redis
migrate
api
bot
worker
beat
nginx
```

# Stage 5 — Payments

Stage 5 adds the production payment domain and provider adapters for:

- Telegram Stars;
- YooMoney;
- YooKassa;
- Platega;
- Crypto Pay / CryptoBot.

The shared payment flow is intentionally provider-independent:

```text
create local pending Payment
        ↓
commit local order
        ↓
create provider invoice/checkout
        ↓
persist provider id / checkout URL
        ↓
provider confirmation
        ↓
verify callback + re-check provider state where supported
        ↓
SELECT ... FOR UPDATE Payment
        ↓
activate_or_extend_purchase(snapshot entitlements)
        ↓
mark Payment paid in the same settlement transaction
```

`payments` has unique constraints on `(provider, external_id)` and
`(provider, idempotency_key)`. Settlement also locks the local payment row, so a repeated provider
callback cannot grant the subscription twice.

A purchase keeps a `plan_snapshot`. Editing or disabling a tariff after checkout creation therefore
does not silently change the duration or quotas of an already-created order.

## Telegram Stars

Digital access offered inside the Telegram bot is exposed only through Telegram Stars (`XTR`).
External providers are not rendered as alternative payment buttons in the bot.

Before enabling Stars:

1. set a positive `plans.price_stars` for every plan you want to sell;
2. enable `telegram_stars` in `payment_provider_settings`;
3. configure `SUPPORT_USERNAME` and your `/terms` destination;
4. test invoice creation, `pre_checkout_query`, `successful_payment` and `/paysupport`.

A successful Telegram charge ID and its raw receipt are persisted before subscription settlement.
This makes a paid order recoverable even if application/database settlement fails after Telegram has
already accepted the payment.

## External provider webhooks

The application accepts provider callbacks at:

```text
POST /api/webhooks/payments/yoomoney
POST /api/webhooks/payments/yookassa
POST /api/webhooks/payments/platega
POST /api/webhooks/payments/cryptopay
```

Use a public HTTPS `PUBLIC_BASE_URL` in production. Provider callbacks are stored in
`payment_webhook_events` before the payment/subscription state is changed. Only a small allow-list
of HTTP headers is stored; authentication secrets are never copied into that table. Actual provider
fees, when supplied by the API, are stored together with `provider_fee_currency` so a crypto-denominated
fee is never silently treated as RUB.

Providers stay disabled after migration until they are explicitly configured. Secrets live only in
`.env`; the database stores operational settings such as `enabled`, `test_mode`, display metadata and
fee assumptions.

### YooMoney

Required environment variables:

```text
YOOMONEY_RECEIVER=
YOOMONEY_NOTIFICATION_SECRET=
```

The local checkout page is:

```text
/checkout/yoomoney/<payment_id>/<checkout_token>
```

It POSTs the payment form to YooMoney and uses `label=pay_<local_payment_id>` for reconciliation.
Incoming HTTP notifications are validated with their HMAC-SHA256 `sign`, RUB currency and accepted
state before access can be granted.

### YooKassa

Required:

```text
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
```

Payment creation uses an `Idempotence-Key`. A webhook by itself is not treated as proof of payment:
the service performs a fresh `GET /payments/{id}` and compares provider amount/currency with the
immutable local order before settlement.

### Platega

Required:

```text
PLATEGA_MERCHANT_ID=
PLATEGA_SECRET=
```

The adapter creates a payment through `/v2/transaction/process`, validates callback credentials in
`X-MerchantId` / `X-Secret`, then reads the current transaction state before settlement.

### Crypto Pay

Required:

```text
CRYPTOPAY_API_TOKEN=
CRYPTOPAY_ACCEPTED_ASSETS=USDT,TON,BTC,ETH,LTC,BNB,TRX,USDC
```

`payment_provider_settings.test_mode=true` switches Crypto Pay to its configured testnet base URL.
Invoices are created with `currency_type=fiat` and the plan's RUB amount. Webhook authenticity is
checked against `crypto-pay-api-signature` using HMAC-SHA256 over the exact raw request body; the
invoice is then fetched from Crypto Pay before subscription settlement.

## Payment provider switches

Stage 7 adds the web UI for provider ON/OFF, test mode, display order and fee assumptions. Provider
credentials still live only in `.env`; the panel shows only whether required credentials appear to
be configured and never renders the secret values. Stars prices remain separate tariff fields.

## Stage 5 database additions

```text
payment_provider_settings
payments
payment_webhook_events
```

Current Alembic head:

```text
20260819_0005
```

## Stage 5 tests

The Stage 5 suite adds checks for:

- YooMoney HMAC-SHA256 verification and tampering;
- Crypto Pay raw-body webhook signature verification;
- Platega callback credential verification;
- repeated YooKassa webhook idempotency;
- disabled-provider callbacks still being processable for previously created payments;
- plan snapshot entitlement reconstruction;
- Stars invoice payload parsing;
- all Stage 1–4 regression tests.

## Next stage

Stage 6 adds referrals and promo codes on top of the now-settled subscription/payment domain.


# Stage 6 — Referrals and promo codes

Stage 6 adds the marketing/reward domain on top of the idempotent payment settlement from Stage 5.

## Referral flow

Each user has a stable link based on the internal user ID:

```text
https://t.me/<BOT_USERNAME>?start=ref_<user_id>
```

The `/referral` command and `🎁 Пригласить друга` button show the link plus invited/paying counts.
A referral is assigned only on the referred user's first registration. The database prevents both
self-referral and assigning more than one referrer to the same user. Invalid or disabled referral
links are stored as a direct registration source rather than being treated as valid referrals.

Default editable `AppSetting` keys:

```text
referral.enabled=true
referral.registration_bonus_requests=0
referral.first_payment_bonus_requests=100
referral.paying_friends_target=3
referral.milestone_reward_days=30
referral.milestone_plan_code=plus
```

The first successful payment of a referred user is recorded exactly once with a locked `Referral`
row. The referrer receives the configured request reward. Every configured number of unique paying
friends creates a days reward. `ReferralReward.idempotency_key` prevents duplicate grants if a
provider repeats a webhook.

Request rewards attach to an active paid subscription. If none exists, they remain `pending` and are
reconciled after the recipient next receives paid access. A milestone days reward can create a real
bonus subscription using `referral.milestone_plan_code`; when applied to an existing subscription it
extends both time and proportional plan quota/token allowance.

## Promo-code flow

Users activate a code with:

```text
/promo PROMO2026
```

A promo code supports:

- enable/disable state;
- start/end dates;
- total activation limit;
- per-user activation limit;
- optional target plan;
- percentage discount;
- fixed RUB discount;
- free days;
- additional ordinary requests;
- additional smart requests.

At most one unreserved promo is waiting for a user's next checkout. Creating a payment locks and
reserves that activation and stores an immutable `promo_snapshot` on the `Payment`. A code that is
already reserved for a pending payment cannot be swapped underneath that order. Definite checkout
creation failures/cancellations/expiry release the reservation; ambiguous network failures keep it
reserved together with the pending payment for later reconciliation.

Percentage discounts work for RUB and Stars. Fixed discounts are RUB-only. Stars discounts are
rounded down to whole Stars. A promo that would reduce a provider checkout to zero is rejected; use
free-days/request benefits rather than a 100% provider discount.

Promo bonuses are granted only during successful payment settlement. This prevents abandoning a
checkout after receiving free days/requests. Free days proportionally increase internal token budget
so the bonus period does not become unusable because of the original period's token ceiling.

## Stage 6 database additions

```text
referrals
referral_rewards
promo_codes
promo_code_activations

payments.original_amount
payments.discount_amount
payments.promo_code_id
payments.promo_snapshot
```

Important invariants include:

- one referrer per referred user;
- DB-level self-referral check;
- unique referral reward idempotency key;
- promo activation status lifecycle `claimed -> reserved -> consumed`;
- one promo activation per payment;
- payment amount breakdown `original_amount = amount + discount_amount`;
- immutable plan and promo snapshots for asynchronous settlement.

Current Alembic head:

```text
20260819_0006
```

## Stage 6 verification

Available local verification:

```text
pytest -ra               50 passed, 3 skipped (redis/aiogram/celery unavailable locally)
python -m compileall     passed
Alembic head             20260819_0006
Alembic offline upgrade  passed
Docker Compose YAML      parsed successfully
```

The execution workspace still has no Docker Engine, so a real `docker compose up` must be run on the
target machine/VPS. Runtime dependencies are already declared for the Docker build.

## Next stage

Stage 8 expands the admin panel with advanced user search/filtering, full user cards, period-based
analytics, economics and charts.


# Stage 7 — Authenticated web admin panel

Stage 7 originally added a server-rendered FastAPI/Jinja admin interface at:

```text
/admin
```

It is intentionally not a heavy SPA. Templates and static CSS are bundled into the same application
image, so no Node build step is required.

## First superadmin

Run migrations and start the stack, then create the first administrator interactively:

```bash
docker compose exec api python -m app.admin.cli create-superadmin
```

or:

```bash
make create-superadmin
```

Passwords must be at least 12 characters and are stored only as Argon2 hashes. Once one active
superadmin exists, additional `admin`/`superadmin` accounts are created from the protected web UI.

## Admin authentication and security

The optional legacy web panel uses:

- a signed `HttpOnly` session cookie;
- `SameSite=Lax`;
- the `Secure` cookie flag in staging/production;
- an 8-hour default session (`ADMIN_SESSION_MAX_AGE_SECONDS=28800`);
- CSRF tokens on every state-changing form, including login/logout;
- Argon2 password hashing;
- role checks for `superadmin`-only administrator creation;
- session rotation after successful login;
- generic login errors;
- AuditLog records for successful/failed login and mutations;
- `X-Frame-Options: DENY`, `nosniff`, referrer/permissions policy headers;
- HSTS in staging/production.

No payment/OpenAI/Telegram API secret is stored in an admin form or rendered into HTML. Provider
credentials remain in environment variables.

## Admin sections implemented

```text
Dashboard
Users
Subscriptions
Payments
Plans
Trial
AI
Referrals
Promo codes
Payment providers
Errors
Audit
Settings
Administrators (superadmin only)
```

Broadcast management is implemented in Stage 9. Subscription/admin notification scheduling remains
Stage 10 rather than being represented by fake placeholder actions.

### Dashboard

The first dashboard includes users/new users/activity, bot-block counts, active subscriptions and
trials, subscriptions expiring within three days, paid revenue windows, OpenAI request/cost summary,
and unresolved error count. Stage 8 will add the full requested analytics, arbitrary periods,
conversion/economics and charts.

### Plans

Plans can now be created and edited from the browser. All price fields, duration, request/smart
request limits, token limits, max output tokens, ordering, recommended flag and ON/OFF state are
editable. Deletion is allowed only when the plan has no subscription/payment/promo references;
otherwise the operator must disable it instead.

### Trial / AI / referrals

Runtime `AppSetting` values are editable with validation. AI model prices can be added or edited in
USD per 1M input/cached/output tokens. The admin panel refuses to switch primary/summary models to a
model that has no pricing row, preventing an unpriced production configuration.

### Payment systems

The panel controls provider ON/OFF, test mode, fee percentage, fixed RUB fee and display order. It
also reports whether the required credential variables are configured without exposing them.
Existing webhook behavior from Stage 5 is unchanged: disabling a provider prevents new checkout
creation but does not invalidate already-created payment callbacks.

### Promo codes

Promos can be created/edited with dates, activation limits, target plan, percentage/fixed RUB
discount, free days, extra ordinary requests and extra smart requests.

### Service settings and maintenance mode

The panel manages project/bot/support labels, greeting/help text and maintenance mode. Greeting,
help/support and maintenance values are read by the live Telegram bot from `AppSetting`.
When maintenance mode is enabled, ordinary users receive the configured maintenance message while
Telegram IDs listed in `ADMIN_TELEGRAM_IDS` continue to pass through.

## Stage 7 migration

```text
20260819_0007_admin_panel.py
```

It adds `admins.last_login_at` and seeds editable service settings such as:

```text
service.name
service.bot_username
service.support_username
service.welcome_text
service.help_text
service.maintenance_mode
service.maintenance_text
```

Current Alembic head:

```text
20260819_0007
```

## Stage 7 verification

```text
pytest -q -ra            58 passed, 3 skipped
python -m compileall     passed
Alembic head             20260819_0007
Alembic offline upgrade  passed
Jinja templates          compiled
```

The three skipped runtime tests are the same environment-only skips for missing local
`redis`, `aiogram` and `celery`. They are declared project dependencies and are installed by the
Docker image. Docker Engine is not available in this execution workspace, so a real Compose boot
still has to be verified on a Docker-capable machine/VPS.

## Next stage

Stage 8: advanced search and filters, detailed per-user cards/actions, user economics, arbitrary
period statistics and dashboard charts.

## Stage 8 — user administration and analytics

Stage 8 adds production-oriented user operations and analytics to the web admin panel:

- combined user search by internal ID, Telegram ID, username, first/last name;
- filters for subscription/trial state, purchase history, plan, payment provider, registration dates, recent activity, bot-blocked and admin-blocked status;
- full user card with access, payment, AI usage, referral, promo and admin-message history;
- manual subscription grant, extension by N days, request quota adjustment, plan change and cancellation;
- trial reset/re-enable and user block/unblock with AuditLog entries;
- direct Telegram message sending from the user card with durable `pending/sent/failed` history;
- optional admin access to dialog text, disabled by default through `privacy.allow_admin_dialog_access`;
- arbitrary Dashboard periods (up to 367 days), daily registrations, purchases, RUB revenue, OpenAI cost, gross profit and subscription-period coverage;
- revenue is grouped by currency and is never summed across RUB/XTR/crypto;
- gross profit in RUB is calculated only when `economics.usd_to_rub` is configured (> 0).

Apply the new schema with:

```bash
alembic upgrade head
```

The Stage 8 migration creates `admin_direct_messages` and seeds the economics/privacy settings.


## Stage 9 — broadcasts and segmentation

Stage 9 adds a durable mass-messaging module to the authenticated web admin panel at:

```text
/admin/broadcasts
```

### Message editor

A broadcast can contain:

- Telegram HTML text;
- an optional JPEG/PNG/WEBP image uploaded through the admin panel;
- URL buttons (`Button text | https://example.com`), one per line;
- a safe browser preview;
- a real Telegram test send to the current admin account only.

The test send requires `admins.telegram_id`. The first superadmin can now be created with an optional
Telegram ID:

```bash
docker compose exec api python -m app.admin.cli create-superadmin --telegram-id 123456789
```

Uploaded broadcast media is stored on the shared Docker volume `broadcast_media` so the FastAPI
container that receives the upload and the Celery worker that sends it see the same file. After a
successful Telegram upload the returned `file_id` is cached in the broadcast and reused for later
recipients.

### Segmentation

All filled audience filters are combined with AND. Users with `bot_blocked=true` or
`is_blocked=true` are always excluded.

Supported filters include:

- all reachable users;
- active paid subscription;
- no active paid subscription;
- active trial;
- trial already ended;
- subscription expiring exactly in N days (including 0/1/2/3);
- subscription expired today, yesterday, or during an arbitrary N-day range;
- never paid / paid at least once;
- active plan;
- payment provider;
- inactive for at least N days;
- registration date range.

Examples for the expired-subscription range fields:

```text
Today          min=0, max=0
Yesterday      min=1, max=1
Last 7 days    min=0, max=7
Last 30 days   min=0, max=30
```

Recipients are materialized into `broadcast_recipients` only when execution actually starts. This
means a scheduled broadcast uses the audience state at send time, while the detail page still shows
a current target estimate before launch.

### Queue, scheduling and stop behavior

Broadcast states are:

```text
draft
scheduled
running
completed
cancelled
failed
```

`Celery worker` performs the actual Telegram sends. A separate `Celery beat` service checks due
scheduled broadcasts every 30 seconds and also re-dispatches stale running jobs for crash recovery.

The admin can:

- save/edit a draft;
- send a test only to themselves;
- launch immediately;
- schedule by UTC date/time;
- request stop while running;
- inspect per-recipient status/error history.

Progress is durable in PostgreSQL:

```text
total
sent
failed
blocked
started_at
finished_at
```

If Telegram reports that a user blocked the bot, the recipient becomes `blocked` and the user row is
updated to `bot_blocked=true` so future broadcasts exclude them.

### Rate limits and retry behavior

The seeded defaults are:

```text
broadcasts.messages_per_second = 25
broadcasts.max_attempts = 4
```

Both are editable from Admin → Settings. The UI restricts the free broadcast speed to 1–30
messages/second. `TelegramRetryAfter` is respected using Telegram's returned `retry_after`; transient
network/server failures use bounded retries. A failure for one recipient does not stop the rest of
the broadcast.

A recipient is committed as `sending` before the Telegram request. If a worker dies in the narrow
uncertain window after Telegram may have accepted the message but before the local success commit,
crash recovery marks that recipient `failed` with an explicit `delivery uncertain` error instead of
automatically resending it and risking a duplicate. Remaining `pending` recipients continue.

### Stage 9 database additions

Migration:

```text
20260819_0009_broadcasts.py
```

New tables:

```text
broadcasts
broadcast_recipients
```

Current Alembic head:

```text
20260819_0009
```

### Stage 9 environment / Docker additions

`.env.example` adds:

```dotenv
BROADCAST_MEDIA_DIR=/data/broadcasts
```

Docker Compose adds:

```text
beat
broadcast_media volume mounted by api + worker
```

### Stage 9 verification

Available verification in the build workspace:

```text
pytest                    72 passed, 3 skipped
python -m compileall      passed
Alembic head              20260819_0009
Alembic 0001 → 0009 SQL   passed
SQLAlchemy metadata       22 tables / PostgreSQL DDL generated
Jinja templates           22 compiled
Docker Compose YAML       parsed successfully
```

The three skipped runtime tests are environment-only skips because this workspace does not have
`redis`, `aiogram` and `celery` installed. They remain declared production dependencies in
`pyproject.toml` and are installed by the Docker build. Docker Engine itself is not available in
this workspace, so a live Compose boot and real Telegram broadcast are not claimed as tested here.

## Stage 10 — notifications and subscription-expiration scheduler

Stage 10 adds durable Telegram notifications for administrators and subscription lifecycle reminders
for users. Notification delivery is now a first-class persisted subsystem rather than a best-effort
`send_message()` call hidden inside business services.

### Administrator notifications

Admin recipients are managed at:

```text
/admin/notifications
```

Each recipient can independently enable/disable:

```text
new user
trial activation
successful purchase
failed payment
OpenAI error
payment-provider error
critical application error
```

If `admin_notification_settings` is empty, the existing `ADMIN_TELEGRAM_IDS` environment setting is
used as a backward-compatible fallback. As soon as at least one database recipient exists, the DB
list becomes the source of truth, including the ability to disable every recipient without changing
`.env`.

The new-user, trial and successful-purchase notifications are emitted only after the corresponding
business transaction has been committed. The purchase notification is tied to the durable
`Payment.status=paid` result, not merely to receipt of a webhook, so an idempotent repeated webhook
does not create a second administrator message.

### Error aggregation and secret hygiene

Repeated errors are grouped by a stable fingerprint in `error_events`. A configurable cooldown
(default: 30 minutes) controls how often the same error may notify administrators while
`occurrence_count` continues increasing.

Before error text/context is persisted or sent to Telegram, common secret shapes and sensitive
context keys are redacted. Telegram error alerts contain a readable category/service/message and do
not include traceback dumps or API credentials. Full sanitized traceback/context remain available in
the admin error view for diagnosis.

The global bot middleware reports unhandled update failures as `critical_error`; FastAPI does the
same for unhandled HTTP failures; broadcast worker failures report through the same subsystem.
OpenAI and payment-provider failures use their dedicated notification categories.

### Subscription lifecycle reminders

The seeded defaults are:

```text
notifications.subscription.enabled = true
notifications.subscription.days_before = [3, 2, 1]
notifications.subscription.expiry_day = true
notifications.subscription.at_expiry = true
notifications.subscription.days_after = [1]
```

The admin UI can change the day lists and edit the HTML message templates. Supported placeholders
are:

```text
{plan_name}
{days}
{expires_date}
{expires_datetime}
```

A reminder includes an inline `👑 Продлить подписку` button that opens the existing subscription
screen in Telegram.

The scheduler runs every minute. For each candidate it reloads and locks the subscription row before
deciding what is due, so a renewal committed after the initial candidate query wins over a stale
expiry candidate. On the calendar expiry date, the explicit expiry-day message has priority over a
late `1 day before` candidate. After the actual `expires_at`, the exact-expiry/post-expiry flow is
used only while the user has not obtained another current paid subscription.

### Deduplication and crash semantics

Every notification is first reserved in `notification_logs` with a globally unique `dedupe_key` and
committed before calling Telegram. Two scheduler instances or concurrent webhook paths therefore
cannot intentionally send the same logical notification twice.

Subscription reminder keys also contain the actual subscription expiration timestamp. Because the
existing subscription row is extended in place on renewal, a later purchased period gets its own new
set of reminders rather than being suppressed by the previous period's logs.

As with broadcasts, an unresolved `pending` notification after a worker interruption is marked
`failed` / `delivery uncertain` by recovery instead of being blindly retried. This deliberately
avoids duplicates in the unavoidable side-effect window where Telegram might have accepted a
message but PostgreSQL did not yet receive the final success commit.

### Stage 10 database additions

Migration:

```text
20260819_0010_notifications.py
```

New tables:

```text
admin_notification_settings
notification_logs
```

`error_events` additionally receives notification aggregation fields and a unique fingerprint
constraint.

Current Alembic head:

```text
20260819_0010
```

SQLAlchemy metadata now contains 24 tables.

### Stage 10 worker schedule

Celery beat now schedules:

```text
subscription notification scan  every 60 seconds
stale notification recovery      every 900 seconds
```

The existing broadcast schedules remain unchanged.

### Stage 10 verification

Available verification in the build workspace:

```text
pytest                    80 passed, 3 skipped
python -m compileall      passed
Alembic head              20260819_0010
Alembic 0001 → 0010 SQL   passed
SQLAlchemy metadata       24 tables / PostgreSQL DDL generated
Jinja templates           23 compiled
Docker Compose YAML       parsed successfully
pyproject.toml            parsed successfully
```

The three skipped runtime tests are environment-only skips because this workspace does not have
`redis`, `aiogram` and `celery` installed. They remain declared dependencies in `pyproject.toml` and
are installed by the Docker build. Docker Engine and live Telegram/provider credentials are not
available in this workspace, so a live Compose boot or real notification send is not claimed as
executed.

## Stage 11 / final release

Stage 11 completes the requested development sequence. No empty Alembic revision was created because
there is no persistent schema change in this stage; the real database head remains
`20260819_0010`.

Final operational documents:

```text
SECURITY_REVIEW.md
PRODUCTION_RUNBOOK.md
FINAL_AUDIT.md
STAGE11_REPORT.md
```

New production tooling:

```text
docker-compose.prod.yml
nginx/production.conf
app/ops/preflight.py
scripts/backup.sh
scripts/restore.sh
scripts/smoke.sh
.github/workflows/ci.yml
```

Security hardening added in the final review includes Redis-backed admin login throttling, explicit
production Host validation, stronger production settings validation, globally redacted JSON logs,
request IDs, CSP with self-hosted admin JavaScript, `no-store` admin responses and a hardened
production Compose overlay.

The final stateful lifecycle test covers registration/referral source, trial activation and usage,
trial expiration, paid settlement, duplicate settlement idempotency, subscription access, an
expiration reminder and early renewal preserving remaining time while adding quota. Existing tests
continue to cover provider webhooks, Stars, promo/referral behavior, broadcasts and notification
deduplication.

Final verification in the build workspace:

```text
pytest                         86 passed, 3 skipped
python -m compileall           passed
Alembic head                   20260819_0010
Alembic 0001 → 0010 SQL        passed
```

The local workspace does not provide Docker Engine or real owner credentials, so full Compose boot,
real Telegram/OpenAI traffic and live payment-provider transactions are release-acceptance steps,
not claimed as executed. Follow `PRODUCTION_RUNBOOK.md` on the target VPS.

## Inline UI / Telegram promo update

The user-facing bot now uses inline keyboards under messages instead of relying on a permanent reply keyboard. Core actions are visible from the main screen, support opens the `SUPPORT_USERNAME` Telegram account, and the admin button is rendered only for `ADMIN_TELEGRAM_IDS`.

Telegram Stars payment remains available when `telegram_stars` is enabled and a plan has `price_stars > 0`.

Telegram Admin → Promo codes now supports a guided instant-subscription promo wizard with name, code, subscription eligibility (`all/first/renewal`), plan, duration, global activation limit and per-user activation limit (`-1` means unlimited). The new schema revision is `20260819_0011`.
