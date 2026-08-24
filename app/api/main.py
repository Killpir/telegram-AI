from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import re
import secrets

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.health import router as health_router
from app.api.payments import router as payments_router
from app.notifications.errors import report_exception
from app.config import get_settings
from app.db.redis import close_redis
from app.db.session import close_db
from app.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_redis()
    await close_db()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.allowed_host_list or ["localhost", "127.0.0.1"],
)

# The browser admin is optional legacy compatibility. By default the project is administered
# entirely from Telegram via ADMIN_TELEGRAM_IDS. Payment callbacks and health routes remain public.
if settings.web_admin_enabled:
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.sessions import SessionMiddleware
    from app.admin.router import router as admin_router
    from app.broadcasts.admin_router import router as broadcasts_admin_router
    from app.notifications.admin_router import router as notifications_admin_router

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie="tg_ai_admin",
        max_age=settings.admin_session_max_age_seconds,
        same_site="lax",
        https_only=settings.app_env in {"staging", "production"},
    )
    admin_static = Path(__file__).resolve().parents[1] / "admin" / "static"
    app.mount("/admin-static", StaticFiles(directory=str(admin_static)), name="admin-static")
    app.include_router(admin_router)
    app.include_router(broadcasts_admin_router)
    app.include_router(notifications_admin_router)


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


@app.middleware("http")
async def security_headers(request, call_next):
    supplied_request_id = request.headers.get("x-request-id", "")
    request_id = supplied_request_id if _REQUEST_ID_RE.fullmatch(supplied_request_id) else secrets.token_hex(16)
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception as exc:
        route = request.scope.get("route")
        route_path = getattr(route, "path", "<unmatched>")
        await report_exception(
            service="api",
            category="critical_error",
            exc=exc,
            settings=settings,
            context={"route": route_path, "method": request.method, "request_id": request_id},
        )
        raise
    response.headers.setdefault("X-Request-ID", request_id)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "form-action 'self' https://yoomoney.ru",
    )
    if request.url.path.startswith("/admin"):
        response.headers.setdefault("Cache-Control", "no-store, max-age=0")
        response.headers.setdefault("Pragma", "no-cache")
    if settings.app_env in {"staging", "production"}:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(health_router)
app.include_router(payments_router)
