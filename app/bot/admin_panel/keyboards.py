from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _rows(items: list[tuple[str, str]], width: int = 2) -> list[list[InlineKeyboardButton]]:
    result: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for text, data in items:
        row.append(InlineKeyboardButton(text=text, callback_data=data))
        if len(row) >= width:
            result.append(row)
            row = []
    if row:
        result.append(row)
    return result


def admin_main_keyboard() -> InlineKeyboardMarkup:
    # Compact two-level navigation inspired by the user's existing VPN bot.
    rows = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:dashboard")],
        [
            InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users"),
            InlineKeyboardButton(text="💳 Платежи", callback_data="adm:payments"),
        ],
        [
            InlineKeyboardButton(text="📣 Рассылка", callback_data="adm:broadcasts"),
            InlineKeyboardButton(text="🎟 Промокоды", callback_data="adm:promos"),
        ],
        [
            InlineKeyboardButton(text="💰 Кредиты", callback_data="adm:credits"),
            InlineKeyboardButton(text="🔌 Платёжные системы", callback_data="adm:providers"),
        ],
        [
            InlineKeyboardButton(text="🎁 Реф. система", callback_data="adm:referrals"),
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="adm:notifications"),
        ],
        [
            InlineKeyboardButton(text="🤖 AI", callback_data="adm:ai"),
            InlineKeyboardButton(text="⚙️ Доп. настройки", callback_data="adm:more"),
        ],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_more_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎁 Стартовый бонус", callback_data="adm:trial"),
                InlineKeyboardButton(text="⚙️ Настройки сервиса", callback_data="adm:settings"),
            ],
            [
                InlineKeyboardButton(text="🚨 Ошибки", callback_data="adm:errors"),
                InlineKeyboardButton(text="🧾 Аудит", callback_data="adm:audit"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main:menu")],
        ]
    )

def back_keyboard(target: str = "adm:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=target)]]
    )


def users_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔎 Найти пользователя", callback_data="adm:users:search")]
    ]
    for item in rows[:10]:
        user = item["user"]
        name = f"@{user.username}" if user.username else (user.first_name or str(user.telegram_id))
        buttons.append(
            [InlineKeyboardButton(text=f"👤 {name} · {user.id}", callback_data=f"adm:user:{user.id}")]
        )
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def user_keyboard(user_id: int, blocked: bool) -> InlineKeyboardMarkup:
    items = [
        ("➕ Кредиты", f"adm:user:addcredits:{user_id}"),
        ("➖ Кредиты", f"adm:user:removecredits:{user_id}"),
        ("🎁 Сбросить стартовый бонус", f"adm:user:trialreset:{user_id}"),
        (("✅ Разблокировать" if blocked else "🚫 Заблокировать"), f"adm:user:block:{user_id}"),
        ("✉️ Написать", f"adm:user:message:{user_id}"),
    ]
    rows = _rows(items, 2)
    rows.append([InlineKeyboardButton(text="⬅️ Пользователи", callback_data="adm:users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def credit_packages_keyboard(packages) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Создать пакет", callback_data="adm:credits:add")]]
    for package in packages:
        icon = "✅" if package.is_active else "⛔"
        rec = " ⭐" if package.is_recommended else ""
        rows.append([InlineKeyboardButton(
            text=f"{icon} {package.name} · {package.total_credits} кр.{rec}",
            callback_data=f"adm:credits:{package.id}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def credit_package_keyboard(package_id: int, active: bool, recommended: bool) -> InlineKeyboardMarkup:
    rows = _rows([
        (("⛔ Выключить" if active else "✅ Включить"), f"adm:credits:toggle:{package_id}"),
        (("⭐ Снять рекомендацию" if recommended else "⭐ Рекомендовать"), f"adm:credits:recommend:{package_id}"),
        ("🏷 Название", f"adm:credits:edit:{package_id}:name"),
        ("📝 Описание", f"adm:credits:edit:{package_id}:description"),
        ("💎 Кредиты", f"adm:credits:edit:{package_id}:credits"),
        ("🎁 Бонус", f"adm:credits:edit:{package_id}:bonus_credits"),
        ("💰 Цена ₽", f"adm:credits:edit:{package_id}:price_rub"),
        ("⭐ Цена Stars", f"adm:credits:edit:{package_id}:price_stars"),
        ("↕️ Порядок", f"adm:credits:edit:{package_id}:sort_order"),
    ], 2)
    rows.append([InlineKeyboardButton(text="⬅️ Пакеты кредитов", callback_data="adm:credits")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ai_modes_keyboard(modes) -> InlineKeyboardMarkup:
    rows = []
    for mode in modes:
        icon = "✅" if mode.is_active else "⛔"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {mode.name} · {mode.credits_per_request} кр./запрос",
            callback_data=f"adm:mode:{mode.id}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ AI", callback_data="adm:ai")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ai_mode_keyboard(mode_id: int, active: bool) -> InlineKeyboardMarkup:
    rows = _rows([
        (("⛔ Выключить" if active else "✅ Включить"), f"adm:mode:toggle:{mode_id}"),
        ("🏷 Название", f"adm:mode:edit:{mode_id}:name"),
        ("📝 Описание", f"adm:mode:edit:{mode_id}:description"),
        ("🤖 Модель", f"adm:mode:edit:{mode_id}:model"),
        ("💎 Цена запроса", f"adm:mode:edit:{mode_id}:credits_per_request"),
        ("📤 Max output", f"adm:mode:edit:{mode_id}:max_output_tokens"),
        ("🧠 Reasoning", f"adm:mode:edit:{mode_id}:reasoning_effort"),
        ("↕️ Порядок", f"adm:mode:edit:{mode_id}:sort_order"),
    ], 2)
    rows.append([InlineKeyboardButton(text="⬅️ Режимы", callback_data="adm:ai:modes")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_keyboard(plans) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="➕ Создать тариф", callback_data="adm:plan:add")]
    ]
    for plan in plans:
        icon = "✅" if plan.is_active else "⛔"
        rows.append(
            [InlineKeyboardButton(text=f"{icon} {plan.name}", callback_data=f"adm:plan:{plan.id}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_keyboard(plan_id: int, active: bool, recommended: bool) -> InlineKeyboardMarkup:
    items = [
        (("⛔ Выключить" if active else "✅ Включить"), f"adm:plan:toggle:{plan_id}"),
        (("⭐ Снять рекомендацию" if recommended else "⭐ Рекомендовать"), f"adm:plan:recommend:{plan_id}"),
        ("🏷 Название", f"adm:plan:edit:{plan_id}:name"),
        ("📝 Описание", f"adm:plan:edit:{plan_id}:description"),
        ("💰 Цена ₽", f"adm:plan:edit:{plan_id}:price_rub"),
        ("⭐ Цена Stars", f"adm:plan:edit:{plan_id}:price_stars"),
        ("💵 Цена USD", f"adm:plan:edit:{plan_id}:price_usd"),
        ("📅 Дней", f"adm:plan:edit:{plan_id}:duration_days"),
        ("💬 Запросов", f"adm:plan:edit:{plan_id}:requests_limit"),
        ("🧠 Умных запросов", f"adm:plan:edit:{plan_id}:smart_requests_limit"),
        ("🤖 Обычная модель", f"adm:plan:edit:{plan_id}:normal_model"),
        ("🧠 Smart модель", f"adm:plan:edit:{plan_id}:smart_model"),
        ("📥 Input tokens", f"adm:plan:edit:{plan_id}:input_tokens_limit"),
        ("📤 Output tokens", f"adm:plan:edit:{plan_id}:output_tokens_limit"),
        ("🗣 Max output", f"adm:plan:edit:{plan_id}:max_output_tokens"),
        ("🗑 Удалить", f"adm:plan:delete:{plan_id}"),
    ]
    rows = _rows(items, 2)
    rows.append([InlineKeyboardButton(text="⬅️ Тарифы", callback_data="adm:plans")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def trial_keyboard(enabled: bool = True, auto: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Количество кредитов", callback_data="adm:setting:credits.trial_bonus:int")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:more")],
    ])


def ai_keyboard() -> InlineKeyboardMarkup:
    items = [
        ("🎚 Режимы AI", "adm:ai:modes"),
        ("💸 Цены моделей", "adm:ai:pricing"),
        ("📝 Summary модель", "adm:setting:ai.summary_model:str"),
        ("🧾 System prompt", "adm:setting:ai.system_prompt:str"),
        ("📚 History", "adm:setting:ai.history_messages:int"),
        ("✂️ Summary trigger", "adm:setting:ai.summary_trigger_messages:int"),
        ("⏱ Timeout", "adm:setting:ai.request_timeout_seconds:float"),
    ]
    rows = _rows(items, 2)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pricing_keyboard(pricings) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"💸 {p.model}", callback_data=f"adm:pricing:{p.id}")]
        for p in pricings[:20]
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить модель", callback_data="adm:pricing:add")])
    rows.append([InlineKeyboardButton(text="⬅️ AI", callback_data="adm:ai")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def providers_keyboard(providers) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for provider in providers:
        icon = "✅" if provider.enabled else "⛔"
        test = "🧪" if provider.test_mode else ""
        rows.append(
            [InlineKeyboardButton(text=f"{icon}{test} {provider.display_name}", callback_data=f"adm:provider:{provider.id}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def provider_keyboard(provider_id: int, enabled: bool, test_mode: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="✏️ Название для пользователей",
                callback_data=f"adm:provider:edit:{provider_id}:display_name",
            )
        ]
    ]
    rows.extend(
        _rows(
            [
                (("⛔ Выключить" if enabled else "✅ Включить"), f"adm:provider:toggle:{provider_id}"),
                (("🧪 Test ON" if test_mode else "🧪 Test OFF"), f"adm:provider:test:{provider_id}"),
                ("% Комиссия", f"adm:provider:edit:{provider_id}:fee_percent"),
                ("₽ Фикс. комиссия", f"adm:provider:edit:{provider_id}:fee_fixed_rub"),
            ],
            2,
        )
    )
    rows.append([InlineKeyboardButton(text="⬅️ Провайдеры", callback_data="adm:providers")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promos_keyboard(promos) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Создать промокод", callback_data="adm:promo:add")],
    ]
    for promo in promos[:15]:
        icon = "✅" if promo.is_active else "⛔"
        title = promo.name or promo.code
        benefit = f"+{promo.additional_credits} кр." if getattr(promo, "additional_credits", 0) else "покупка"
        rows.append([InlineKeyboardButton(text=f"{icon} {title} · {benefit}", callback_data=f"adm:promo:{promo.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_keyboard(promo_id: int, active: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=("⛔ Выключить" if active else "✅ Включить"), callback_data=f"adm:promo:toggle:{promo_id}")],
        [
            InlineKeyboardButton(text="🏷 Название", callback_data=f"adm:promo:edit:{promo_id}:name"),
            InlineKeyboardButton(text="🎫 Код", callback_data=f"adm:promo:edit:{promo_id}:code"),
        ],
        [
            InlineKeyboardButton(text="💎 Кредиты", callback_data=f"adm:promo:edit:{promo_id}:additional_credits"),
            InlineKeyboardButton(text="🔢 Активаций", callback_data=f"adm:promo:edit:{promo_id}:max_activations"),
        ],
        [
            InlineKeyboardButton(text="👤 На пользователя", callback_data=f"adm:promo:edit:{promo_id}:per_user_limit"),
            InlineKeyboardButton(text="⏳ Срок действия", callback_data=f"adm:promo:validity:{promo_id}"),
        ],
        [InlineKeyboardButton(text="⬅️ Промокоды", callback_data="adm:promos")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_scope_keyboard(*, prefix: str, current: str | None = None) -> InlineKeyboardMarkup:
    labels = [("all", "👥 Для всех"), ("first", "🆕 Только первая"), ("renewal", "🔁 Не первая")]
    rows = []
    for value, label in labels:
        mark = "✅ " if value == current else ""
        rows.append([InlineKeyboardButton(text=mark + label, callback_data=f"{prefix}:{value}")])
    rows.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data="adm:promos")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_plan_keyboard(plans, *, prefix: str, back: str = "adm:promos") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"👑 {plan.name}", callback_data=f"{prefix}:{plan.id}")] for plan in plans]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Создать промокод", callback_data="adm:promo:create:confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:promos")],
        ]
    )

def referrals_keyboard(enabled: bool, level2_enabled: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=("⛔ Выключить рефералку" if enabled else "✅ Включить рефералку"),
                callback_data="adm:ref:toggle",
            )
        ],
        [
            InlineKeyboardButton(
                text=(
                    "🟢 2-й уровень: ON"
                    if level2_enabled
                    else "⚪ 2-й уровень: OFF"
                ),
                callback_data="adm:ref:level2:toggle",
            )
        ],
        [
            InlineKeyboardButton(
                text="1️⃣ Регистрация",
                callback_data="adm:setting:referral.registration_bonus_credits:int",
            ),
            InlineKeyboardButton(
                text="1️⃣ Первая покупка",
                callback_data="adm:setting:referral.first_payment_bonus_credits:int",
            ),
        ],
        [
            InlineKeyboardButton(
                text="2️⃣ Регистрация",
                callback_data="adm:setting:referral.level2_registration_bonus_credits:int",
            ),
            InlineKeyboardButton(
                text="2️⃣ Первая покупка",
                callback_data="adm:setting:referral.level2_first_payment_bonus_credits:int",
            ),
        ],
        [
            InlineKeyboardButton(
                text="👥 Порог друзей",
                callback_data="adm:setting:referral.paying_friends_target:int",
            ),
            InlineKeyboardButton(
                text="💎 Бонус за порог",
                callback_data="adm:setting:referral.milestone_reward_credits:int",
            ),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcasts_keyboard(rows_data) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Новая рассылка", callback_data="adm:broadcast:add")]]
    for row in rows_data[:12]:
        rows.append([InlineKeyboardButton(text=f"📣 #{row.id} {row.status}: {row.name[:28]}", callback_data=f"adm:broadcast:{row.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_keyboard(broadcast_id: int, status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status in {"draft", "scheduled"}:
        rows.append([
            InlineKeyboardButton(text="▶️ Запустить сейчас", callback_data=f"adm:broadcast:start:{broadcast_id}"),
            InlineKeyboardButton(text="🕒 Запланировать", callback_data=f"adm:broadcast:schedule:{broadcast_id}"),
        ])
        rows.append([InlineKeyboardButton(text="🔗 URL-кнопки", callback_data=f"adm:broadcast:buttons:{broadcast_id}")])
    if status in {"scheduled", "running"}:
        rows.append([InlineKeyboardButton(text="⏹ Остановить", callback_data=f"adm:broadcast:stop:{broadcast_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Рассылки", callback_data="adm:broadcasts")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def audience_keyboard() -> InlineKeyboardMarkup:
    items = [
        ("👥 Всем", "adm:broadcast:aud:all"),
        ("💎 Есть баланс", "adm:broadcast:aud:balance_positive"),
        ("0️⃣ Баланс 0", "adm:broadcast:aud:balance_zero"),
        ("💸 Покупали", "adm:broadcast:aud:paid"),
        ("🆕 Не покупали", "adm:broadcast:aud:never"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=_rows(items, 2))


def notifications_keyboard(settings_rows: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in settings_rows:
        icon = "✅" if row.enabled else "⛔"
        rows.append([InlineKeyboardButton(text=f"{icon} {row.telegram_id}", callback_data=f"adm:notif:{row.id}")])
    rows.append([InlineKeyboardButton(text="🔄 Синхронизировать из ENV", callback_data="adm:notif:sync")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notification_keyboard(row) -> InlineKeyboardMarkup:
    fields = [
        ("notify_new_user", "👤 Новый пользователь"),
        ("notify_trial", "🎁 Стартовый бонус"),
        ("notify_purchase", "💰 Покупка"),
        ("notify_payment_failed", "❌ Неусп. платёж"),
        ("notify_openai_error", "🤖 OpenAI error"),
        ("notify_payment_error", "💳 Payment error"),
        ("notify_critical_error", "🚨 Critical"),
    ]
    rows = [[InlineKeyboardButton(text=f"{'✅' if getattr(row, field) else '⛔'} {label}", callback_data=f"adm:notif:field:{row.id}:{field}")] for field, label in fields]
    rows.append([InlineKeyboardButton(text=("⛔ Получатель OFF" if row.enabled else "✅ Получатель ON"), callback_data=f"adm:notif:enabled:{row.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Уведомления", callback_data="adm:notifications")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def subscription_notifications_keyboard(config) -> InlineKeyboardMarkup:
    items = [
        (("⛔ Выключить" if config.enabled else "✅ Включить"), "adm:notif:subtoggle:enabled"),
        (("📅 День окончания ON" if config.expiry_day else "📅 День окончания OFF"), "adm:notif:subtoggle:expiry_day"),
        (("⏰ В момент окончания ON" if config.at_expiry else "⏰ В момент окончания OFF"), "adm:notif:subtoggle:at_expiry"),
        ("⬅️ Дни до", "adm:setting:notifications.subscription.days_before:listint"),
        ("➡️ Дни после", "adm:setting:notifications.subscription.days_after:listint"),
    ]
    rows = _rows(items, 2)
    rows.append([InlineKeyboardButton(text="⬅️ Уведомления", callback_data="adm:notifications")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notification_templates_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="⏳ До окончания", callback_data="adm:tpl:before")],
        [InlineKeyboardButton(text="📅 В день окончания", callback_data="adm:tpl:expiryday")],
        [InlineKeyboardButton(text="❌ Закончилась", callback_data="adm:tpl:expired")],
        [InlineKeyboardButton(text="🤖 После окончания", callback_data="adm:tpl:after")],
        [InlineKeyboardButton(text="⬅️ Уведомления", callback_data="adm:notifications")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def settings_keyboard(maintenance: bool) -> InlineKeyboardMarkup:
    items = [
        (("🛠 Maintenance ON" if maintenance else "🟢 Maintenance OFF"), "adm:settings:maintenance"),
        ("🏷 Название", "adm:setting:service.name:str"),
        ("🤖 Username бота", "adm:setting:service.bot_username:str"),
        ("🆘 Support", "adm:setting:service.support_username:str"),
        ("👋 Приветствие", "adm:setting:service.welcome_text:str"),
        ("❓ Help", "adm:setting:service.help_text:str"),
        ("💱 USD/RUB", "adm:setting:economics.usd_to_rub:float"),
        ("📣 Рассылка/с", "adm:setting:broadcasts.messages_per_second:int"),
    ]
    rows = _rows(items, 2)
    rows.append([InlineKeyboardButton(text="📄 Соглашение и политика", callback_data="adm:legal")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:more")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def legal_buttons_keyboard(*, agreement_enabled: bool, privacy_enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'🟢' if agreement_enabled else '🔴'} Соглашение",
                    callback_data="adm:legal:agreement",
                ),
                InlineKeyboardButton(
                    text=f"{'🟢' if privacy_enabled else '🔴'} Политика",
                    callback_data="adm:legal:privacy",
                ),
            ],
            [InlineKeyboardButton(text="⬅️ Настройки", callback_data="adm:settings")],
        ]
    )


def legal_button_keyboard(kind: str, *, enabled: bool) -> InlineKeyboardMarkup:
    if kind not in {"agreement", "privacy"}:
        raise ValueError("Unknown legal button kind")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("🟢 Включена" if enabled else "🔴 Выключена"),
                    callback_data=f"adm:legal:{kind}:toggle",
                )
            ],
            [
                InlineKeyboardButton(text="✏️ Текст кнопки", callback_data=f"adm:legal:{kind}:text"),
                InlineKeyboardButton(text="🔗 Ссылка", callback_data=f"adm:legal:{kind}:url"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:legal")],
        ]
    )
