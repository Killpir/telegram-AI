from aiogram import Router

from app.bot.handlers import ai_mode, chat, fallback, help, new_dialog, payments, profile, promocode, referral, start, subscription
from app.bot.admin_panel.router import router as admin_panel_router


def build_root_router() -> Router:
    router = Router(name="root")
    router.include_router(start.router)
    router.include_router(new_dialog.router)
    router.include_router(profile.router)
    router.include_router(subscription.router)
    router.include_router(ai_mode.router)
    router.include_router(referral.router)
    router.include_router(promocode.router)
    router.include_router(payments.router)
    router.include_router(help.router)
    router.include_router(admin_panel_router)
    router.include_router(chat.router)
    router.include_router(fallback.router)
    return router


__all__ = ["build_root_router"]
