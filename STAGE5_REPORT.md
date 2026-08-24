# Stage 5 report — payment architecture

## Scope

Stage 5 implements the payment layer required by the project specification:

- common `PaymentProvider` interface;
- `Payment`, `PaymentProviderSetting`, `PaymentWebhookEvent` persistence;
- Telegram Stars;
- YooMoney;
- YooKassa;
- Platega;
- Crypto Pay / CryptoBot;
- idempotent settlement;
- provider enable/disable state;
- plan purchase snapshots;
- external webhooks;
- activation/renewal of subscriptions only after confirmed payment.

## Architecture

New payment code is isolated under `app/payments/`:

```text
app/payments/
├── base.py
├── factory.py
├── repository.py
├── service.py
├── stars.py
├── yoomoney.py
├── yookassa.py
├── platega.py
├── cryptopay.py
└── utils.py
```

Telegram handlers call `PaymentService`; they do not contain provider business logic. FastAPI owns
external checkout/webhook endpoints.

## Database

Migration:

```text
20260819_0005_payments.py
```

Adds:

```text
payment_provider_settings
payments
payment_webhook_events
```

Important constraints:

- unique `(provider, external_id)`;
- unique `(provider, idempotency_key)`;
- unique checkout token;
- provider/status CHECK constraints;
- payment/user/plan/provider/date indexes;
- FK to user, plan and resulting subscription;
- provider fee amount plus its currency when the provider exposes an actual fee.

All providers are seeded disabled. This is intentional: credentials and Stars prices must be set
explicitly before a payment method can be shown/used.

## Idempotency and settlement safety

Payment settlement locks the local payment row with PostgreSQL `FOR UPDATE` and checks its current
status before changing subscription state. A callback arriving more than once therefore does not
extend the subscription more than once.

The local pending payment is committed before making an external create-payment call. This leaves a
durable reconciliation record if an external API succeeds but the application is interrupted before
it receives the response. Ambiguous transport failures remain `pending` with a diagnostic error so a
later authenticated webhook can still settle them; definite configuration/API rejections are marked
`failed`.

Webhook payloads are persisted before subscription settlement. For Telegram Stars, the
`telegram_payment_charge_id` and raw successful-payment receipt are persisted before subscription
settlement as well, so a charge is not lost merely because later application settlement fails.

## Purchase snapshots

Each payment stores an immutable `plan_snapshot` containing the purchased duration and limits.
`SubscriptionService.activate_or_extend_purchase()` consumes this snapshot instead of the current
editable plan values. A plan edited or disabled after checkout creation therefore cannot change the
entitlements of that already-created order.

## Telegram Stars

Implemented:

- XTR invoices;
- empty provider token;
- protected invoice payload containing local payment ID + random checkout token;
- `pre_checkout_query` validation;
- successful-payment settlement;
- persistence of Telegram charge ID;
- duplicate settlement protection;
- provider refund method through Telegram Bot API;
- `/paysupport`, `/support`, `/terms` commands.

Only Stars is rendered as a purchase method inside Telegram. External providers remain outside the
bot payment UI.

## YooMoney

Implemented:

- local external checkout page;
- official form POST flow;
- local `label=pay_<id>` reconciliation;
- HTTP notification parsing;
- HMAC-SHA256 `sign` verification;
- RUB/accepted-state validation;
- operation ID persistence;
- fee derivation from transferred vs credited amount;
- duplicate callback-safe settlement.

## YooKassa

Implemented:

- payment creation;
- redirect confirmation URL;
- UUID idempotency key;
- webhook handling;
- fresh provider status lookup before settlement;
- amount/currency verification;
- raw webhook persistence;
- refund adapter;
- duplicate webhook safety.

## Platega

Implemented:

- `/v2/transaction/process` payment creation;
- payment URL persistence;
- callback authentication through merchant/secret headers;
- current transaction status lookup before settlement;
- amount/currency verification;
- cancel/refund adapter;
- duplicate callback safety.

## Crypto Pay

Implemented:

- fiat-denominated RUB invoice creation;
- configurable accepted crypto assets;
- mainnet/testnet selection from provider `test_mode`;
- invoice status lookup;
- `invoice_paid` webhook handling;
- HMAC-SHA256 verification over the exact raw request body;
- local payment ID in invoice payload;
- duplicate callback-safe settlement.

## Provider switches and secrets

Operational provider state is stored in `payment_provider_settings`:

```text
enabled
test_mode
fee_percent
fee_fixed_rub
sort_order
```

Provider credentials are not stored in this table. They remain environment secrets in `.env`.
The Stage 7 admin panel will expose the ON/OFF/test-mode controls without exposing full secrets.

## API routes

```text
GET  /checkout/yoomoney/{payment_id}/{checkout_token}
GET  /checkout/result
POST /api/webhooks/payments/yoomoney
POST /api/webhooks/payments/yookassa
POST /api/webhooks/payments/platega
POST /api/webhooks/payments/cryptopay
```

## Configuration added

See `.env.example` for:

```text
SUPPORT_USERNAME
TERMS_URL
PAYMENT_HTTP_TIMEOUT_SECONDS
YOOMONEY_RECEIVER
YOOMONEY_NOTIFICATION_SECRET
YOOKASSA_SHOP_ID
YOOKASSA_SECRET_KEY
PLATEGA_MERCHANT_ID
PLATEGA_SECRET
CRYPTOPAY_API_TOKEN
CRYPTOPAY_ACCEPTED_ASSETS
```

## Verification performed

Available local verification for this stage:

```text
pytest                   36 passed
python compileall        passed
Alembic head             20260819_0005
Alembic offline upgrade  passed
```

The complete Alembic chain from the initial revision through Stage 5 generates PostgreSQL SQL
successfully.

`ruff` could not be installed in the execution environment because outbound package downloads are
blocked. Python compilation and the complete available test suite were still executed successfully.

The environment does not provide Docker Engine, so `docker compose up` cannot be executed inside
this workspace. The project remains configured to install runtime dependencies and run through
Docker Compose on the target VPS.

## Deferred according to the project stage order

Not falsely marked as complete in Stage 5:

- web admin UI for payment switches/fees — Stage 7;
- admin purchase/error Telegram notifications — Stage 10;
- financial dashboard and per-user payment analytics UI — Stages 7–8;
- promo/referral benefits — Stage 6.

## Next

Stage 6: referral system and promo codes.
