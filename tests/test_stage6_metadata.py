from app.db import Base


def test_stage6_tables_are_registered() -> None:
    expected = {
        "referrals",
        "referral_rewards",
        "promo_codes",
        "promo_code_activations",
    }
    assert expected <= set(Base.metadata.tables)


def test_payment_has_promo_snapshot_and_amount_breakdown() -> None:
    payment = Base.metadata.tables["payments"]
    assert {"original_amount", "discount_amount", "promo_code_id", "promo_snapshot"} <= set(
        payment.c.keys()
    )
