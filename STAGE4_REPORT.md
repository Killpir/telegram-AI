# Stage 4 report — plans, trial and subscriptions

## Goal

Implement Stage 4 from the project specification without weakening Stage 1–3: database-managed plans, configurable one-time trial access, paid subscription entities/services, access gating for AI, real profile/subscription UI, migrations and tests.

## Created

```text
app/db/models/access.py
app/plans/repository.py
app/plans/service.py
app/subscriptions/config.py
app/subscriptions/repository.py
app/subscriptions/service.py
app/subscriptions/access.py
app/bot/keyboards/subscription.py
app/bot/handlers/subscription.py
alembic/versions/20260818_0004_plans_trials_subscriptions.py
tests/test_subscription_service.py
tests/test_trial_service.py
tests/test_access_service.py
STAGE4_REPORT.md
```

## Updated

```text
app/db/models/user.py
app/db/models/__init__.py
app/ai/service.py
app/ai/summarizer.py
app/bot/handlers/start.py
app/bot/handlers/profile.py
app/bot/handlers/chat.py
app/bot/handlers/help.py
app/bot/handlers/__init__.py
app/bot/keyboards/main.py
app/bot/main.py
app/notifications/admin.py
pyproject.toml
README.md
```

## Database design

### Plan

`Plan` is now the source of tariff configuration. It contains:

- code/name/description;
- `price_rub`, `price_stars`, `price_usd`;
- duration days;
- ordinary and smart request quotas;
- internal input/output token quotas;
- per-plan max output tokens;
- JSON feature flags;
- display order;
- recommended/active flags.

Initial `Lite`, `Plus`, `Max` rows are inserted by Alembic only as editable seed data.

### Trial

Trial history is separated from `User`. `users.trial_used` represents one-time eligibility, while each trial row records its activation snapshot and usage. This allows a future admin reset to restore eligibility without erasing prior trial history.

A PostgreSQL partial unique index prevents more than one `active` trial per user.

### Subscription

Subscription stores its accumulated entitlement and usage rather than relying on current plan quotas for historical purchases. This makes early renewal economically correct: another purchased period adds time and allowance.

A PostgreSQL partial unique index prevents more than one `active` subscription per user.

## Renewal rule

Implemented and tested:

```text
new_expires_at = max(now, current_expires_at) + duration_days
```

If the active subscription has 20 days remaining and the user purchases another 30-day period, the result is 50 days from now, not 30.

The purchased plan's ordinary/smart request and internal token entitlements are also added. Existing usage remains unchanged.

## Trial behavior

Default seed configuration:

```text
trial.enabled = true
trial.duration_days = 3
trial.requests_limit = 20
trial.smart_requests_limit = 0
trial.input_tokens_limit = 250000
trial.output_tokens_limit = 80000
trial.auto_activate = false
notifications.admin.trial_activation_enabled = true
```

Manual activation is available from the Telegram subscription screen. Automatic activation can be enabled in the DB setting and is applied on `/start`.

Activation locks the user row before checking/changing `trial_used`, so concurrent activation attempts cannot legitimately consume trial twice. The DB active-trial unique index is an additional invariant.

A paid subscriber cannot activate/auto-activate trial and accidentally waste the one-time trial entitlement.

## AI integration

Stage 3 previously allowed every registered user through global anti-abuse limits. Stage 4 now adds entitlement checks inside the existing per-user Redis conversation lease.

Order:

1. validate message size;
2. acquire per-user conversation lease;
3. resolve paid subscription, then trial;
4. verify access quotas;
5. verify model pricing;
6. apply global request/token anti-abuse limits;
7. summarize if needed;
8. charge summary token overhead to current access;
9. call OpenAI;
10. persist message/AI usage/cost;
11. charge one successful request plus chat tokens to current access;
12. commit while still holding the conversation lease.

No-access users therefore do not cause OpenAI spend and do not consume the global per-minute request bucket.

Failed OpenAI calls do not consume the user-visible trial/subscription request counter.

## Telegram UI

Added:

```text
/subscription
👑 Подписка
🎁 Активировать пробный период
```

The subscription view reads active plans from PostgreSQL. It shows the current entitlement and usage but deliberately has no fake purchase button before Stage 5 providers exist.

`/profile` now shows the real plan/trial, expiration and request usage.

Trial activation removes the one-time inline activation button and sends a confirmation with expiration time. Admins receive a Telegram notification when the corresponding setting is enabled.

## Expiration handling

Stage 10 will introduce scheduled expiration/notifications. Until then, Stage 4 performs safe lazy reconciliation: whenever access/profile is resolved, an `active` row whose `expires_at <= now` is changed to `expired` before access is granted.

This is sufficient for authorization correctness even before the scheduler exists.

## Security / integrity choices

- plan data is not hardcoded in Telegram handlers;
- user row is locked for one-time trial activation;
- active trial/subscription uniqueness is enforced in PostgreSQL;
- paid access takes priority over trial;
- plan deletion is protected by `subscriptions.plan_id ... ON DELETE RESTRICT`;
- no payment foreign key is invented before the Stage 5 `Payment` model exists;
- plan/admin-controlled text is HTML-escaped before Telegram rendering;
- access is checked before OpenAI spend.

## Verification

### Unit tests

Final local result:

```text
28 passed
```

The suite covers the new Stage 4 cases plus all earlier tests.

### Python syntax

```text
python -m compileall app tests alembic
OK
```

### SQLAlchemy PostgreSQL DDL

`Plan`, `Trial` and `Subscription` metadata all compile with the PostgreSQL dialect, including partial indexes.

```text
METADATA_DDL_OK
```

### Alembic

```text
alembic heads
20260818_0004 (head)
```

Full offline upgrade from the first revision through Stage 4 succeeds:

```text
alembic upgrade head --sql
OK
```

The generated SQL includes all Stage 4 tables, constraints, partial unique indexes and seed rows.

### Docker Compose

The YAML parses successfully and still contains:

```text
postgres
redis
migrate
api
bot
worker
nginx
```

This execution environment does not provide a Docker Engine/CLI, so an actual container boot cannot be performed here. Run the final integration check on a Docker-capable machine with:

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
docker compose logs -f migrate bot
```

## Not implemented yet by design

These belong to later specification stages rather than Stage 4:

- actual purchase/payment buttons;
- `Payment` table and payment FK;
- Telegram Stars/YooMoney/YooKassa/Platega/Crypto Pay;
- smart-mode request execution (the quota fields are ready);
- admin web UI for editing plans/trial settings;
- proactive expiration scheduler and 3/2/1-day notifications.

No fake provider or placeholder purchase flow was added.

## Next

Stage 5: modular payment provider architecture, payment persistence/idempotency, Telegram Stars, YooMoney, YooKassa, Platega and Crypto Pay, then activation/extension through the Stage 4 subscription service only after confirmed payment.
