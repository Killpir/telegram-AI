# Final Functional Audit

Date: 2026-08-19

## Automated lifecycle audit

`tests/test_stage11_full_lifecycle.py` exercises the core lifecycle in one stateful test:

```text
new user
→ referral start parameter
→ trial activation
→ AI access grant
→ trial request/token usage accounting
→ trial expiration blocks AI
→ paid settlement
→ subscription activation
→ duplicate settlement does not grant twice
→ paid access replaces trial
→ 3-day expiration reminder becomes due
→ early renewal
→ old expiration reminder is no longer due
→ expiration extends from the existing future expiration
→ purchased quotas accumulate
```

## Requirements audit matrix

### Registration and bot

- `/start` create/update: covered by `test_user_service_flow.py`.
- registration source/referral parameter: covered by `test_user_service_flow.py` and Stage 11 lifecycle test.
- menu/profile/help: source/runtime tests in bot test suite.
- admin first-user notification: notification service and Stage 10 tests.

### Trial

- one-time trial: `test_trial_service.py`.
- paid subscription blocks trial activation: `test_trial_service.py`.
- trial expiration fallback/block: `test_access_service.py` and Stage 11 lifecycle test.
- trial admin notification: Stage 10 notification tests/service.

### AI

- Responses API parsing/usage fields: `test_ai_client.py`.
- context construction and summarization boundaries: `test_ai_context.py`.
- model pricing/cost: `test_ai_pricing.py`.
- internal rate/token limits: `test_ai_limits.py`.
- access quotas: `test_access_service.py`.
- dialog/message metadata: `test_ai_model_metadata.py`.

### Subscription

- expiration math: `test_subscription_service.py`.
- early renewal keeps remaining time: `test_subscription_service.py` and Stage 11 lifecycle test.
- quotas accumulate on renewal: `test_subscription_service.py` and Stage 11 lifecycle test.

### Payments

- provider signature/security primitives: `test_payment_security.py`.
- settlement/idempotency: `test_payment_service.py`.
- duplicate HTTP webhook does not double-activate: `test_payment_service.py`.
- duplicate Telegram Stars success does not double-activate: `test_payment_service.py`.
- immutable plan snapshot: `test_payment_service.py`.
- provider/setting/payment schema constraints: model metadata tests.

### Referrals and promo codes

- self-referral/reward behavior: `test_referral_service.py`.
- first-payment reward idempotency: referral/payment tests.
- discount and entitlement snapshots: `test_promocode_service.py`.
- claim/reserve/consume lifecycle: `test_promocode_service.py`.

### Admin

- password hashing/CSRF/session helpers: `test_admin_security.py`.
- template integrity: `test_admin_templates.py`.
- Stage 7 metadata/routes: `test_stage7_admin_metadata.py`.
- user search/analytics/actions: `test_stage8_admin_analytics.py`.
- Stage 11 login throttling and production config validation: `test_stage11_security_ops.py`.

### Broadcasts

- durable queue/progress model: `test_stage9_broadcasts.py`.
- segment SQL/filter combinations: `test_stage9_broadcasts.py`.
- unsafe URL rejection: `test_stage9_broadcasts.py`.
- content limits and worker recovery/rate-limit behavior: `test_stage9_broadcasts.py`.

### Notifications

- admin settings/log schema: `test_stage10_notifications.py`.
- subscription before/day/expired/after event selection: `test_stage10_notifications.py`.
- duplicate versioning changes after extension: `test_stage10_notifications.py`.
- error redaction/fingerprint stability: `test_stage10_notifications.py` and Stage 11 logging test.

### Production/security

- production config rejects HTTP public URL, wildcard hosts and placeholder credentials: Stage 11 tests.
- global JSON log redaction: Stage 11 tests.
- inline admin JS removed for self-only CSP: Stage 11 tests.
- Nginx broadcast upload limit aligned with application limit: Stage 11 tests.
- production Compose hardening files exist and are statically verified: Stage 11 tests.
- CI definition includes real PostgreSQL/Redis and Alembic online upgrade.

## Live verification still required on target infrastructure

The following cannot be truthfully marked as executed in the build workspace:

- Docker Engine boot of the complete Compose stack;
- real Telegram bot update delivery;
- real OpenAI API call with the owner's key;
- Telegram Stars test purchase/refund;
- YooMoney/YooKassa/Platega/Crypto Pay sandbox/production payments;
- certificate issuance and external HTTPS reachability;
- backup restoration against the target VPS storage;
- external e-mail/SMS/monitoring because those integrations are not part of this project.

These are release-acceptance steps in `PRODUCTION_RUNBOOK.md`, not silently assumed successes.
