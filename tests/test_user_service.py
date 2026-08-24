from app.users.service import UserService


def test_start_parameter_is_trimmed_and_limited() -> None:
    value = "  ref_" + "x" * 400 + "  "
    normalized = UserService._normalize_start_parameter(value)
    assert normalized is not None
    assert normalized.startswith("ref_")
    assert len(normalized) == 256


def test_registration_source_detects_referral() -> None:
    assert UserService._registration_source("ref_123") == "referral"
    assert UserService._registration_source("campaign_summer") == "direct"
    assert UserService._registration_source(None) == "direct"
