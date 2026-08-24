from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _support_url(username: str | None) -> str | None:
    value = (username or "").strip().lstrip("@")
    return f"https://t.me/{value}" if value else None


def _http_url(value: str | None) -> str | None:
    candidate = (value or "").strip()
    return candidate if candidate.startswith(("https://", "http://")) else None


def main_menu_keyboard(
    *,
    is_admin: bool = False,
    support_username: str | None = None,
    # `terms_url` is kept as a backward-compatible fallback for installations that
    # configured the old single "Условия" button only through .env.
    terms_url: str | None = None,
    agreement_enabled: bool | None = None,
    agreement_text: str = "📄 Соглашение",
    agreement_url: str | None = None,
    privacy_enabled: bool = False,
    privacy_text: str = "🔐 Политика",
    privacy_url: str | None = None,
    trial_available: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="🤖 Выбрать режим", callback_data="ai:mode"),
            InlineKeyboardButton(text="💰 Баланс", callback_data="main:balance"),
        ],
        [InlineKeyboardButton(text="💬 Новый диалог", callback_data="main:new")],
    ]
    if trial_available:
        rows.append(
            [InlineKeyboardButton(text="🎁 Получить бесплатные кредиты", callback_data="credits:trial")]
        )

    support = _support_url(support_username)
    social = [InlineKeyboardButton(text="🎁 Пригласить друга", callback_data="main:referral")]
    if support:
        social.append(InlineKeyboardButton(text="🆘 Поддержка", url=support))
    else:
        social.append(InlineKeyboardButton(text="🆘 Поддержка", callback_data="main:support"))
    rows.append(social)

    rows.append([InlineKeyboardButton(text="❓ Помощь", callback_data="main:help")])

    # Legal buttons are intentionally a separate row so they stay easy to find and
    # can be managed independently from the rest of the main menu.
    legacy_terms_url = _http_url(terms_url)
    agreement_link = _http_url(agreement_url) or legacy_terms_url
    agreement_visible = bool(agreement_enabled) if agreement_enabled is not None else bool(legacy_terms_url)
    privacy_link = _http_url(privacy_url)

    legal_row: list[InlineKeyboardButton] = []
    if agreement_visible and agreement_link:
        legal_row.append(
            InlineKeyboardButton(text=(agreement_text or "📄 Соглашение").strip(), url=agreement_link)
        )
    if privacy_enabled and privacy_link:
        legal_row.append(
            InlineKeyboardButton(text=(privacy_text or "🔐 Политика").strip(), url=privacy_link)
        )
    if legal_row:
        rows.append(legal_row)

    if is_admin:
        rows.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
