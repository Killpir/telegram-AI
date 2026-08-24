# Production Runbook

This runbook targets a single Ubuntu/VPS deployment using Docker Compose.

## 1. Host prerequisites

Install and keep updated:

- Docker Engine;
- Docker Compose plugin;
- Git (if deploying from a repository);
- a host-level ACME client such as Certbot, or another way to supply TLS certificate files;
- basic firewall rules allowing only SSH, HTTP and HTTPS from the public Internet.

Do not expose PostgreSQL or Redis host ports.

## 2. DNS and TLS

Create an A/AAAA record for the service domain pointing to the VPS.

The production Nginx overlay expects a directory containing:

```text
fullchain.pem
privkey.pem
```

Set:

```env
TLS_CERT_DIR=/absolute/path/to/certificate-directory
```

The certificate must cover the hostname in `PUBLIC_BASE_URL`.

## 3. Configure `.env`

Copy:

```bash
cp .env.example .env
chmod 600 .env
```

Minimum production values:

```env
APP_ENV=production
SECRET_KEY=<strong random value, >=32 chars>
WEBHOOK_SECRET=<strong random value, >=24 chars>
BOT_TOKEN=<BotFather token>
OPENAI_API_KEY=<OpenAI API key>

POSTGRES_PASSWORD=<strong unique password>
DATABASE_URL=postgresql+asyncpg://ai_bot:<URL-ENCODED-PASSWORD>@postgres:5432/ai_bot

PUBLIC_BASE_URL=https://bot.example.com
ALLOWED_HOSTS=bot.example.com,127.0.0.1
ADMIN_TELEGRAM_IDS=123456789
```

Useful random generators:

```bash
openssl rand -hex 32
openssl rand -base64 48
```

Never paste production secrets into the web admin. Provider credentials remain environment variables.

## 4. Validate Compose before starting

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config >/tmp/compose.rendered.yml
```

Inspect the rendered configuration and make sure secrets/paths resolve as expected.

## 5. First boot

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The one-shot `migrate` service must finish successfully before API/bot/worker services start.

Check:

```bash
docker compose ps
docker compose logs migrate --tail=200
docker compose logs api bot worker beat nginx --tail=200
```

## 6. Configure Telegram administrator

The normal deployment does not require a browser superadmin. Set at least one trusted personal Telegram ID in `.env`:

```env
ADMIN_TELEGRAM_IDS=123456789
WEB_ADMIN_ENABLED=false
```

Restart the bot after changing this list. The `⚙️ Админ-панель` button and `/admin` handlers are available only to IDs in this value.

If you deliberately enable the legacy web interface with `WEB_ADMIN_ENABLED=true`, then create its separate superadmin with the existing CLI.

## 7. Production preflight

```bash
docker compose exec api python -m app.ops.preflight
```

It verifies:

- `APP_ENV=production`;
- production Pydantic configuration;
- PostgreSQL connectivity;
- Redis connectivity;
- database Alembic revision equals source head;
- at least one Telegram administrator is configured in `ADMIN_TELEGRAM_IDS`;
- when `WEB_ADMIN_ENABLED=true`, at least one active legacy web superadmin exists;
- active AI pricing exists for the configured primary and summary models.

Warnings do not fail the check; failures do.

## 8. Smoke test

```bash
./scripts/smoke.sh https://bot.example.com
```

It checks `/health/live` and `/health/ready`. If `WEB_ADMIN_ENABLED=true`, it can additionally check the optional `/admin/login` route when the environment variable is exported for the smoke script.

Do not treat this as a substitute for Telegram/provider end-to-end testing.

## 9. Configure application from Telegram admin

Before opening to users, open `/admin` in the Telegram bot:

1. Verify AI primary/summary model names and current prices.
2. Configure trial limits.
3. Configure plans and Stars prices.
4. Configure notification recipients and templates.
5. Configure referral/promo defaults if needed.
6. Configure provider credentials in `.env`, then enable only providers whose credentials were tested.
7. Verify `economics.usd_to_rub` if RUB gross-profit reporting is required.
8. Keep `privacy.allow_admin_dialog_access=false` unless policy explicitly permits message review.

## 10. Payment-provider acceptance tests

For every provider you intend to enable, test at minimum:

- create checkout/invoice;
- cancel or expire a checkout;
- successful payment;
- repeated webhook/event;
- amount/currency mismatch rejection;
- provider disabled after checkout creation;
- successful subscription activation;
- second purchase before current expiration;
- user/admin Telegram notification after payment;
- reconciliation of a callback stored in `payment_webhook_events`.

Do not enable a provider for customers until its own sandbox/test-mode path has passed.

## 11. Backups

Create a logical PostgreSQL dump and broadcast-media archive:

```bash
./scripts/backup.sh
```

Artifacts are created under `./backups/`.

Copy them off-host to encrypted storage. A backup kept only on the same VPS is not a disaster-recovery backup.

Recommended minimum for a small paid service:

- daily database backup;
- multiple retained generations;
- off-host copy;
- periodic restore test.

## 12. Restore drill

Restoration is intentionally destructive and requires an explicit flag:

```bash
RESTORE_CONFIRM=YES ./scripts/restore.sh backups/postgres-YYYYMMDDTHHMMSSZ.dump \
  backups/broadcast-media-YYYYMMDDTHHMMSSZ.tar.gz
```

The script stops public/application services first and leaves them stopped after restoration.

Then:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api bot worker beat nginx
docker compose exec api python -m app.ops.preflight
./scripts/smoke.sh https://bot.example.com
```

Verify at least one known user, subscription and payment manually before reopening marketing traffic.

## 13. Safe update procedure

Before each release:

```bash
./scripts/backup.sh
```

Then update source and inspect release changes.

Build first without replacing running services:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
```

Review migration SQL if a new migration exists:

```bash
alembic upgrade head --sql | less
```

Apply migrations:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate
```

Deploy:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Then:

```bash
docker compose exec api python -m app.ops.preflight
./scripts/smoke.sh https://bot.example.com
docker compose logs --since=10m api bot worker beat nginx
```

Database rollback is migration-specific. Do not assume every schema/data migration can safely be downgraded after new application code has written data.

## 14. Logs and diagnostics

Useful commands:

```bash
docker compose ps
docker compose logs -f --tail=200 api
docker compose logs -f --tail=200 bot
docker compose logs -f --tail=200 worker
docker compose logs -f --tail=200 beat
docker compose logs -f --tail=200 nginx
docker compose logs -f --tail=200 postgres
docker compose logs -f --tail=200 redis
```

Application logs are JSON and include redaction of common secret forms.

Check `/admin/errors` and `/admin/audit` for application-level incidents and privileged changes.

## 15. Health semantics

`/health/live` means the API process is running.

`/health/ready` checks PostgreSQL and Redis. Use readiness, not liveness, for deciding whether the service can accept normal traffic.

## 16. TLS renewal

After renewing host certificate files, reload Nginx:

```bash
docker compose exec nginx nginx -t
docker compose exec nginx nginx -s reload
```

Automate this hook with the ACME client used on the VPS.

## 17. Incident response basics

If an API/payment incident occurs:

1. Enable maintenance mode if user actions should stop.
2. Disable affected payment provider(s) in admin UI; existing callbacks are still processed for already-created checkouts.
3. Preserve logs and `payment_webhook_events` before manually editing payment state.
4. Never manually extend a user merely because a callback was seen; verify provider state/amount/currency first.
5. If a secret is suspected leaked, rotate it at the provider and update `.env`, then restart affected services.
6. If database integrity is in doubt, stop writes before restoration/reconciliation.

## 18. Scaling notes

For a small deployment one VPS is acceptable. As traffic grows, separate managed PostgreSQL/Redis, external object storage for broadcast media, multiple workers, centralized logs/metrics, off-host backups, health monitoring and a load-balanced API tier should be considered before simply increasing container counts.
