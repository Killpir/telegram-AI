# Security Review — Stage 11

Date: 2026-08-19

This review covers the application, admin panel, payment callbacks, AI integration, workers, Docker deployment and operational tooling. It is a practical review for a small commercial SaaS deployment, not a formal penetration test or compliance certification.

## Implemented controls

### Secrets and configuration

- Runtime credentials stay in environment variables and are not stored in `AppSetting`.
- `SecretStr` is used for core secrets in Pydantic settings.
- Production/staging startup rejects placeholder/short `SECRET_KEY` and `WEBHOOK_SECRET` values.
- Production/staging requires `BOT_TOKEN`, `OPENAI_API_KEY`, an absolute HTTPS `PUBLIC_BASE_URL`, explicit `ALLOWED_HOSTS`, and a non-placeholder database password.
- `.env` remains ignored by Git; `.env.example` contains placeholders only.

### Admin authentication

- Passwords use Argon2.
- Admin passwords are bounded to 12–256 characters so oversized login bodies do not reach Argon2 verification.
- Session is rotated on successful login.
- Admin cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` in staging/production.
- All state-changing admin forms use CSRF validation.
- Admin login is rate-limited per `(client IP, username)` using Redis.
- In staging/production the login limiter fails closed if Redis is unavailable.
- `superadmin`-only actions are enforced server-side.
- Admin mutations are recorded in `AuditLog`.

### HTTP security

- `TrustedHostMiddleware` is enabled for configured hosts.
- Production requires explicit host names; wildcard hosts are rejected.
- Responses set `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, and CSP headers.
- Production/staging adds HSTS.
- Admin responses use `Cache-Control: no-store`.
- CSP uses `script-src 'self'`; inline admin scripts and inline event handlers were removed in Stage 11.
- Every API response receives an `X-Request-ID`. A syntactically safe incoming request ID is preserved; otherwise a random ID is generated.

### Logging and error handling

- JSON logs globally redact common bearer tokens, OpenAI-style keys, Telegram bot tokens and sensitive structured keys.
- Error events sanitize text/context before persistence and notification.
- Payment/webhook notification failures do not roll back a successfully committed payment.
- Repeated application errors are aggregated by fingerprint and notification cooldown.
- Telegram admin error messages do not include raw tracebacks or API credentials.

### Telegram and AI

- User messages are length-limited before OpenAI calls.
- Per-minute, per-day, per-month and token limits protect API economics.
- Plan/trial access is checked before an AI request.
- Dialog text access from the web admin is disabled by default and must be explicitly enabled.
- Telegram rate-limit responses are handled in broadcast delivery.

### Payments

- Payment providers share one interface and are independently enabled/disabled.
- Existing callbacks continue to process after a provider is disabled so in-flight purchases are not lost.
- `(provider, external_id)` and provider idempotency keys are unique.
- Settlement locks the payment row before activating access.
- Repeat webhooks and repeat Telegram `successful_payment` updates do not extend access twice.
- Purchase plan/promo entitlements are snapshotted before checkout so later tariff edits cannot alter an in-flight purchase.
- Provider-specific callback authenticity/status verification is implemented where supported.
- Raw webhook events are persisted for reconciliation before settlement.
- Only a sanitized allow-list of HTTP headers is persisted with payment webhook diagnostics.

### Database and workers

- SQLAlchemy ORM is used for application queries; user input is not interpolated into raw SQL.
- Alembic is the only production schema creation/update mechanism.
- Partial unique indexes protect one active trial/subscription/dialog per user.
- Broadcast and notification queues persist recipient/log state before external Telegram side effects.
- Recovery favors avoiding duplicate user-facing messages when delivery state is uncertain.

### Containers

- The application image runs as a non-root `app` user.
- `/data/broadcasts` is created with ownership suitable for that user before the named volume is mounted.
- Production Compose overlay sets application services to read-only root filesystems, adds `no-new-privileges`, drops Linux capabilities and provides dedicated tmpfs mounts.
- PostgreSQL and Redis are not published to host ports in the normal application Compose file.
- Nginx is the only public HTTP entry point.

## Stage 11 fixes discovered during review

1. **Admin brute-force protection was missing.** Added Redis login throttling.
2. **Structured application logs could leak secrets through arbitrary `extra` fields.** Added global redaction.
3. **Admin CSP could not be strict because templates contained inline JavaScript/event handlers.** Moved behavior to `/admin-static/admin.js` and enabled `script-src 'self'`.
4. **Production accepted arbitrary Host headers.** Added trusted-host validation and production validation for `ALLOWED_HOSTS`.
5. **Production configuration could still use HTTP public URLs and obvious placeholder credentials.** Startup now rejects these values.
6. **Broadcast uploads allowed 10 MB in application logic while Nginx rejected requests above 2 MB.** Raised reverse-proxy body limit to 12 MB.
7. **Broadcast named volume could be root-owned while application containers run as non-root.** Image now pre-creates/chowns `/data/broadcasts`.
8. **No reusable production backup/restore/smoke/preflight tooling existed.** Added scripts and operational runbook.

## Residual risks / deliberate scope limits

These are not hidden or represented as solved:

- Live payment credentials and real Telegram/OpenAI traffic were not available in the build workspace, so provider sandbox/production transactions still require deployment testing.
- This is a single-host Docker Compose architecture. It does not provide HA PostgreSQL, Redis Sentinel/Cluster, multi-node workers, automatic failover or zero-downtime database migrations.
- `pg_dump` backups are logical snapshots, not point-in-time recovery. For higher RPO/RTO requirements, add WAL archiving/base backups or a managed PostgreSQL backup service.
- Broadcast-media backup is separate from the PostgreSQL dump; for strict cross-resource atomicity use maintenance mode or object storage/versioning.
- The project uses bounded dependency ranges rather than a complete transitive lockfile. Before a controlled release, generate and commit a lock/constraints artifact in CI and scan that exact dependency set.
- No WAF/CDN/DDoS protection is bundled. Put the VPS behind an appropriate provider if exposure warrants it.
- TLS certificate issuance/renewal is host/environment-specific. The production Nginx config expects mounted `fullchain.pem` and `privkey.pem` files.
- User timezone is not stored; subscription notifications are based on UTC-normalized timestamps.
- The bot currently uses long polling. If switched to Telegram webhooks later, use Telegram's webhook `secret_token` verification as part of that change.
- No formal SAST/DAST, third-party penetration test, PCI assessment or legal/compliance review has been performed.

## Release decision

The codebase is suitable for a **controlled production pilot** after the deployment checklist is completed, credentials are configured, provider sandbox flows are exercised, backups are tested by restoration, and smoke/preflight checks pass on the target VPS.

## Telegram-native admin addendum

The primary administrative trust boundary is now `ADMIN_TELEGRAM_IDS` from the deployment environment. The reply-keyboard button is only a convenience: `/admin`, state handlers and all `adm:*` callbacks independently verify the current Telegram sender ID. Audit rows produced by Telegram administration store the actor Telegram ID in sanitized JSON details because no browser `admins` row is required.

The legacy web panel is opt-in (`WEB_ADMIN_ENABLED=false` by default). Disabling it does not disable FastAPI, Nginx, health endpoints or external payment callbacks.

Administrator notification preference rows are constrained by the current environment allow-list: a DB row for an ID removed from `ADMIN_TELEGRAM_IDS` no longer authorizes privileged notifications.
