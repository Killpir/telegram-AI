# Stage 6 report — referrals and promo codes

## Scope

Stage 6 implements the next project-specification block:

- referral links `ref_<id>`;
- immutable referrer assignment;
- self-referral protection;
- configurable registration/first-payment/milestone rewards;
- reward history and idempotency;
- `/referral` and referral stats in Telegram;
- configurable promo codes;
- activation limits and date windows;
- plan-specific promos;
- percentage/fixed discounts;
- bonus days and additional requests;
- promo reservation during checkout;
- immutable promo snapshot on `Payment`;
- promo/referral settlement integrated with all Stage 5 payment providers.

## New modules

```text
app/referrals/
├── config.py
├── repository.py
└── service.py

app/promocodes/
├── repository.py
└── service.py

app/bot/handlers/
├── referral.py
└── promocode.py

app/db/models/
├── referral.py
└── promo.py
```

## Referral registration

The bot generates links in the form:

```text
https://t.me/<bot_username>?start=ref_<internal_user_id>
```

On the first `/start`, `ReferralService.register_from_start()` validates the source and persists the
referral in the same transaction as the new user registration. Invalid IDs, disabled referrals,
missing referrers and self-referrals do not create a referral row. The referred user's source is
repaired back to `direct` for invalid `ref_...` parameters.

Database protection:

```text
UNIQUE(referred_user_id)
CHECK(referrer_user_id <> referred_user_id)
UNIQUE(referrer_user_id, referred_user_id)
```

A repeated `/start` cannot replace the original referrer because referral assignment only runs for a
newly created user and the DB also enforces one referrer per referred user.

## Referral rewards

Migration seeds editable settings:

```text
referral.enabled = true
referral.registration_bonus_requests = 0
referral.first_payment_bonus_requests = 100
referral.paying_friends_target = 3
referral.milestone_reward_days = 30
referral.milestone_plan_code = "plus"
```

Every reward is stored in `referral_rewards` with a unique `idempotency_key` and `pending/applied`
status. The first successful payment of the referred user locks their `Referral` row and sets
`first_paid_at`; therefore two successful payments settling concurrently cannot both create the
"first payment" reward.

Default behavior:

- registration reward: disabled (`0` requests);
- first successful payment: +100 requests to the referrer;
- every 3 unique paying referrals: +30 subscription days.

If the referrer has an active paid subscription, request rewards increase its request allowance.
Day rewards extend expiration and add proportional plan request/smart/token entitlement. If a
milestone is reached while the referrer has no paid subscription, a real bonus subscription can be
created using the configurable `referral.milestone_plan_code`. Request-only rewards without active
paid access remain pending until paid access exists.

## Promo-code model

`promo_codes` supports:

```text
code
is_active
starts_at / ends_at
max_activations
per_user_limit
plan_id (optional)
discount_percent
discount_fixed_rub
free_days
additional_requests
additional_smart_requests
```

`promo_code_activations` stores the history and checkout lifecycle:

```text
claimed
reserved
consumed
expired
```

A user activates a code with:

```text
/promo PROMO2026
```

Only one unreserved claim is kept waiting for the next checkout. A newly activated code expires the
previous unreserved claim, while an activation already reserved by a pending payment remains frozen
to that payment.

## Payment integration

Stage 6 extends `payments` with:

```text
original_amount
discount_amount
promo_code_id
promo_snapshot
```

and enforces:

```text
original_amount >= amount
discount_amount >= 0
original_amount = amount + discount_amount
```

Payment creation now:

```text
load plan + provider
        ↓
find latest eligible claimed promo
        ↓
validate code is still enabled/in date
        ↓
calculate discounted amount
        ↓
create local Payment with plan_snapshot + promo_snapshot
        ↓
reserve PromoCodeActivation for payment_id
        ↓
commit local order
        ↓
call provider
```

This keeps the Stage 5 guarantee that local state exists before an external side effect. If a
provider definitively rejects creation, the promo reservation is released. Ambiguous transport
failures remain pending/reserved because the provider may still have created the invoice and later
send an authenticated webhook.

For Telegram Stars, existing invoices remain processable if the admin disables the provider or plan
after invoice creation; only new checkout creation is blocked. An expired Stars invoice releases its
reserved promo.

## Discount rules

- percentage discounts work for RUB and XTR;
- fixed discounts are denominated in RUB and are ignored for Stars;
- Stars discounts are rounded down to a whole Star;
- total discount is capped at the original amount;
- a provider checkout reduced to zero is rejected; 100%-free campaigns should use free days/requests.

## Successful settlement

All provider settlement paths still converge on `PaymentService._settle_locked()`.

Before activating the subscription, promo snapshot bonuses modify the purchased entitlement:

- `free_days` extends duration;
- `additional_requests` increases ordinary requests;
- `additional_smart_requests` increases smart requests;
- free days proportionally increase internal input/output token allowance.

After paid access exists:

- pending referral rewards belonging to the buyer are reconciled;
- the promo activation is marked `consumed` exactly once;
- the buyer's referral is marked first-paid exactly once;
- rewards for their referrer are issued idempotently;
- the payment is marked `paid`.

Repeated provider callbacks still short-circuit on the already-paid `Payment` and do not repeat any
subscription, promo or referral benefit.

## Telegram UX

New command/menu behavior:

```text
/referral
🎁 Пригласить друга
/promo CODE
```

`/referral` shows the personal link, invited count, paying count and current reward rules. The
subscription screen explains how to activate a promo code. The command menu/help now lists both
commands.

## Migration

New revision:

```text
20260819_0006_referrals_promocodes.py
```

Current head:

```text
20260819_0006
```

The full offline PostgreSQL upgrade from `20260818_0001` through `20260819_0006` was generated
successfully.

## Verification

Final available test result:

```text
50 passed, 3 skipped
```

The three skipped tests require packages not installed in this execution environment:

```text
redis
aiogram
celery
```

Those dependencies remain declared for the actual Docker build.

Additional checks:

```text
python -m compileall app alembic tests   passed
Alembic head                              20260819_0006
Alembic full offline SQL                  passed
SQLAlchemy metadata                       19 tables, Stage 6 tables present
Docker Compose YAML                       parsed successfully
```

Docker Engine is not available in this workspace, so `docker compose up` cannot be truthfully
reported as executed here.

## Deferred by the project stage order

Not falsely marked complete yet:

- CRUD/admin UI for referrals and promo codes — Stage 7;
- full user/filter/financial analytics UI — Stage 8;
- referral/promo broadcast segmentation — Stage 9 where useful;
- admin Telegram notifications around purchase/error events — Stage 10.

## Next

Stage 7: authenticated production web admin panel.
