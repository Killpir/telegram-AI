from pathlib import Path

from app.db.models import PromoCode
from app.promocodes import PromoCodeService


def test_main_navigation_is_inline_and_exposes_core_actions() -> None:
    source = Path("app/bot/keyboards/main.py").read_text()
    assert "InlineKeyboardMarkup" in source
    assert 'callback_data="main:new"' in source
    assert 'callback_data="main:profile"' in source
    assert 'callback_data="main:subscription"' in source
    assert 'callback_data="main:promo"' in source
    assert 'text="🆘 Поддержка", url=support' in source
    assert 'callback_data="adm:main"' in source


def test_subscription_screen_keeps_telegram_stars_purchase_buttons() -> None:
    source = Path("app/bot/keyboards/subscription.py").read_text()
    assert 'callback_data=f"pay:stars:{plan.id}"' in source
    assert "price_stars" in source
    assert "Stars" in source


def test_promo_schema_supports_vpn_style_subscription_rules() -> None:
    promo = PromoCode(
        code="TEST",
        name="Тестовый промокод",
        is_active=True,
        grant_on_activation=True,
        subscription_scope="first",
        per_user_limit=-1,
        free_days=7,
        additional_requests=0,
        additional_smart_requests=0,
    )
    snap = PromoCodeService.snapshot(promo)
    assert snap["name"] == "Тестовый промокод"
    assert snap["grant_on_activation"] is True
    assert snap["subscription_scope"] == "first"


def test_promo_admin_has_guided_creation_and_current_activation_counter() -> None:
    keyboard = Path("app/bot/admin_panel/keyboards.py").read_text()
    router = Path("app/bot/admin_panel/router.py").read_text()
    assert "➕ Промокод на подписку" in keyboard
    assert "Только первая" in keyboard
    assert "Не первая" in keyboard
    assert "Текущих активаций" in router
    assert "PromoCreate.name" in router
    assert "PromoCreate.per_user_limit" in router


def test_migration_0011_changes_per_user_limit_to_allow_minus_one() -> None:
    migration = Path("alembic/versions/20260819_0011_inline_promo_ui.py").read_text()
    assert 'revision: str = "20260819_0011"' in migration
    assert 'down_revision: str | None = "20260819_0010"' in migration
    assert "per_user_limit = -1 OR per_user_limit > 0" in migration
    assert "subscription_scope IN ('all','first','renewal')" in migration
