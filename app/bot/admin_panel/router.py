from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.admin_panel.access import is_env_admin
from app.bot.admin_panel.keyboards import (
    admin_main_keyboard,
    admin_more_keyboard,
    ai_keyboard,
    ai_mode_keyboard,
    ai_modes_keyboard,
    audience_keyboard,
    back_keyboard,
    broadcast_keyboard,
    broadcasts_keyboard,
    credit_package_keyboard,
    credit_packages_keyboard,
    notification_keyboard,
    notification_templates_keyboard,
    notifications_keyboard,
    subscription_notifications_keyboard,
    plan_keyboard,
    plans_keyboard,
    pricing_keyboard,
    promo_confirm_keyboard,
    promo_keyboard,
    promo_plan_keyboard,
    promo_scope_keyboard,
    promos_keyboard,
    provider_keyboard,
    providers_keyboard,
    referrals_keyboard,
    settings_keyboard,
    legal_buttons_keyboard,
    legal_button_keyboard,
    trial_keyboard,
    user_keyboard,
    users_keyboard,
)
from app.bot.admin_panel.service import TelegramAdminError, TelegramAdminService
from app.bot.admin_panel.states import AdminInput, BroadcastDraft, PromoCreate
from app.config import Settings
from app.db.models import AIModelMode, AIModelPricing, Broadcast, CreditPackage, PaymentProviderSetting, Plan, PromoCode
from app.payments.utils import payment_provider_configured

router = Router(name="telegram_admin")
service = TelegramAdminService()
logger = logging.getLogger(__name__)


LEGAL_BUTTONS = {
    "agreement": {
        "title": "📄 Соглашение",
        "enabled_key": "legal.agreement.enabled",
        "text_key": "legal.agreement.text",
        "url_key": "legal.agreement.url",
        "default_text": "📄 Соглашение",
    },
    "privacy": {
        "title": "🔐 Политика",
        "enabled_key": "legal.privacy.enabled",
        "text_key": "legal.privacy.text",
        "url_key": "legal.privacy.url",
        "default_text": "🔐 Политика",
    },
}


def _legal_defaults(kind: str, settings: Settings) -> tuple[bool, str, str]:
    item = LEGAL_BUTTONS[kind]
    fallback_url = settings.terms_url if kind == "agreement" else None
    url = str(fallback_url or "").strip()
    return bool(url), str(item["default_text"]), url


async def _legal_config(session: AsyncSession, settings: Settings, kind: str) -> dict[str, object]:
    if kind not in LEGAL_BUTTONS:
        raise TelegramAdminError("Неизвестная юридическая кнопка")
    item = LEGAL_BUTTONS[kind]
    values = await service.get_settings(
        session,
        [str(item["enabled_key"]), str(item["text_key"]), str(item["url_key"])],
    )
    default_enabled, default_text, default_url = _legal_defaults(kind, settings)
    enabled_raw = values.get(str(item["enabled_key"]))
    text_raw = values.get(str(item["text_key"]))
    url_raw = values.get(str(item["url_key"]))
    return {
        "kind": kind,
        "title": item["title"],
        "enabled": bool(enabled_raw) if enabled_raw is not None else default_enabled,
        "text": str(default_text if text_raw is None else text_raw),
        "url": str(default_url if url_raw is None else url_raw),
    }


def _legal_detail_text(config: dict[str, object]) -> str:
    enabled = bool(config["enabled"])
    url = str(config["url"] or "")
    return (
        f"{html.escape(str(config['title']))} <b>— настройка кнопки</b>\n\n"
        f"Статус: {'🟢 видна пользователям' if enabled else '🔴 скрыта'}\n"
        f"Текст: <b>{html.escape(str(config['text']))}</b>\n"
        f"Ссылка: {html.escape(url) if url else '—'}\n\n"
        "Можно менять только видимость, текст и ссылку."
    )

def _money(value) -> str:
    try:
        return f"{Decimal(str(value or 0)):.2f}"
    except Exception:
        return str(value or 0)


def _dt(value) -> str:
    if value is None:
        return "—"
    try:
        return value.astimezone().strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(value)


def _admin(settings: Settings, telegram_id: int | None) -> bool:
    return is_env_admin(settings, telegram_id)


async def _deny_message(message: Message) -> None:
    await message.answer("Команда недоступна.")


async def _deny_callback(callback: CallbackQuery) -> None:
    await callback.answer("Недостаточно прав.", show_alert=True)


async def _edit_or_answer(callback: CallbackQuery, text: str, markup=None) -> None:
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(text, reply_markup=markup)
            return
        except Exception:
            await callback.message.answer(text, reply_markup=markup)
            return


async def _show_main_message(message: Message) -> None:
    await message.answer(
        "⚙️ <b>Админ-панель</b>\n\nУправление сервисом выполняется прямо из Telegram.",
        reply_markup=admin_main_keyboard(),
    )


@router.message(Command("admin"))
async def admin_command(message: Message, settings: Settings, state: FSMContext) -> None:
    if not _admin(settings, message.from_user.id if message.from_user else None):
        await _deny_message(message)
        return
    await state.clear()
    await _show_main_message(message)


@router.message(F.text == "⚙️ Админ-панель")
async def admin_button(message: Message, settings: Settings, state: FSMContext) -> None:
    if not _admin(settings, message.from_user.id if message.from_user else None):
        await _deny_message(message)
        return
    await state.clear()
    await _show_main_message(message)


async def _dashboard_text(session: AsyncSession) -> str:
    snap = await service.dashboard(session)
    return (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{snap['users']['total']}</b>\n"
        f"├ сегодня: {snap['users']['today']}\n"
        f"├ 7 дней: {snap['users']['d7']}\n"
        f"└ активны сегодня: {snap['users']['active_today']}\n\n"
        f"⭐ Stars сегодня: <b>{_money(snap['stars']['today'])}</b>\n"
        f"⭐ Stars за 7 дней: {_money(snap['stars']['d7'])}\n"
        f"⭐ Stars за 30 дней: {_money(snap['stars']['d30'])}\n"
        f"💰 RUB за 30 дней: {_money(snap['money']['d30'])}\n\n"
        f"🤖 AI-запросов: {snap['ai']['requests']}\n"
        f"💸 OpenAI 30 дней: ${_money(snap['ai']['cost_30_usd'])}\n"
        f"🚨 Нерешённых ошибок: {snap['errors_open']}"
    )


async def _user_text(session: AsyncSession, user_id: int) -> tuple[str, object] | None:
    data = await service.user_detail(session, user_id)
    if data is None:
        return None
    user = data["user"]
    sub = data.get("active_subscription")
    plan = data.get("active_plan")
    trial = data.get("active_trial")
    stats = data.get("stats", {})
    name = " ".join(filter(None, [user.first_name, user.last_name])) or "—"
    username = f"@{user.username}" if user.username else "—"
    lines = [
        f"👤 <b>Пользователь #{user.id}</b>",
        "",
        f"Telegram ID: <code>{user.telegram_id}</code>",
        f"Username: {html.escape(username)}",
        f"Имя: {html.escape(name)}",
        f"Регистрация: {_dt(user.created_at)}",
        f"Последняя активность: {_dt(user.last_activity_at)}",
        f"Статус: {'🚫 заблокирован' if user.is_blocked else '✅ активен'}",
        f"Bot blocked: {'да' if user.bot_blocked else 'нет'}",
        "",
    ]
    balance = await service.user_credit_balance(session, user_id)
    starter_bonus = int(await service.get_setting(session, "credits.trial_bonus", 20) or 0)
    starter_bonus_status = (
        "использован"
        if user.trial_used
        else f"доступен (<b>{starter_bonus} кредитов</b>)"
    )
    lines.extend([
        f"💰 Баланс: <b>{balance} кредитов</b>",
        f"🎁 Стартовый бонус: {starter_bonus_status}",
    ])
    lines.extend(
        [
            "",
            f"💳 Успешных платежей: {stats.get('payments_count', 0)}",
            f"💰 RUB revenue: {_money((stats.get('revenue_by_currency') or {}).get('RUB', 0))}",
            f"🤖 AI запросов: {stats.get('ai_requests_all', 0)}",
            f"💸 AI cost: ${_money(stats.get('ai_cost_all_usd', 0))}",
        ]
    )
    return "\n".join(lines), user


def _plan_text(plan: Plan) -> str:
    return (
        f"👑 <b>{html.escape(plan.name)}</b> <code>{html.escape(plan.code)}</code>\n\n"
        f"Статус: {'✅ ON' if plan.is_active else '⛔ OFF'}\n"
        f"Рекомендуемый: {'⭐ да' if plan.is_recommended else 'нет'}\n"
        f"Цена: <b>{_money(plan.price_rub)} ₽</b>\n"
        f"Stars: {plan.price_stars if plan.price_stars is not None else '—'}\n"
        f"Дней: {plan.duration_days}\n"
        f"Запросов: {plan.requests_limit}\n"
        f"Умных запросов: {plan.smart_requests_limit}\n"
        f"Обычная модель: <code>{html.escape(str((plan.features or {}).get('normal_model') or 'gpt-5.6-luna'))}</code>\n"
        f"Smart модель: <code>{html.escape(str((plan.features or {}).get('smart_model') or '—'))}</code>\n"
        f"Input tokens: {plan.input_tokens_limit}\n"
        f"Output tokens: {plan.output_tokens_limit}\n"
        f"Max output: {plan.max_output_tokens}"
    )



def _credit_package_text(package: CreditPackage) -> str:
    stars = package.price_stars if package.price_stars is not None else "—"
    bonus = f" + {package.bonus_credits} бонус" if package.bonus_credits else ""
    return (
        f"💰 <b>{html.escape(package.name)}</b> <code>{html.escape(package.code)}</code>\n\n"
        f"Статус: {'✅ ON' if package.is_active else '⛔ OFF'}\n"
        f"Рекомендуемый: {'⭐ да' if package.is_recommended else 'нет'}\n"
        f"Кредиты: <b>{package.credits}{bonus}</b>\n"
        f"Итого: <b>{package.total_credits}</b>\n"
        f"Цена: <b>{_money(package.price_rub)} ₽</b>\n"
        f"Stars: <b>{stars}</b>\n"
        f"Описание: {html.escape(package.description or '—')}"
    )


def _mode_text(mode: AIModelMode) -> str:
    return (
        f"🎚 <b>{html.escape(mode.name)}</b> <code>{html.escape(mode.code)}</code>\n\n"
        f"Статус: {'✅ ON' if mode.is_active else '⛔ OFF'}\n"
        f"Пользовательская цена: <b>{mode.credits_per_request} кредит(а)/запрос</b>\n"
        f"Модель: <code>{html.escape(mode.model)}</code>\n"
        f"Reasoning: <code>{html.escape(mode.reasoning_effort)}</code>\n"
        f"Max output: {mode.max_output_tokens}\n"
        f"Описание: {html.escape(mode.description or '—')}"
    )

def _scope_label(value: str) -> str:
    return {"all": "Для всех", "first": "Только первая", "renewal": "Не первая"}.get(value, value)


def _promo_text(promo: PromoCode, *, activations: int = 0, plan_name: str | None = None) -> str:
    max_acts = "∞" if promo.max_activations is None else str(promo.max_activations)
    per_user = "∞" if promo.per_user_limit == -1 else str(promo.per_user_limit)
    if promo.grant_on_activation and getattr(promo, "additional_credits", 0) > 0:
        return (
            f"🎟 <b>{html.escape(promo.name or promo.code)}</b>\n\n"
            f"Код: <code>{html.escape(promo.code)}</code>\n"
            f"Статус: {'✅ ON' if promo.is_active else '⛔ OFF'}\n"
            f"Награда: <b>+{promo.additional_credits} кредитов</b>\n"
            f"Количество активаций: <b>{max_acts}</b>\n"
            f"На пользователя: <b>{per_user}</b>\n"
            f"Действует до: <b>{_dt(promo.ends_at) if promo.ends_at else 'без ограничения'}</b>\n"
            f"Текущих активаций: <b>{activations}</b>"
        )
    if promo.grant_on_activation:
        return (
            f"🎟 <b>{html.escape(promo.name or promo.code)}</b>\n\n"
            f"Код: <code>{html.escape(promo.code)}</code>\n"
            f"Статус: {'✅ ON' if promo.is_active else '⛔ OFF'}\n"
            f"Тип подписки: <b>{html.escape(_scope_label(promo.subscription_scope))}</b>\n"
            f"Тариф: <b>{html.escape(plan_name or '—')}</b>\n"
            f"Длительность: <b>{promo.free_days} дней</b>\n"
            f"Количество активаций: <b>{max_acts}</b>\n"
            f"На пользователя: <b>{per_user}</b>\n"
            f"Текущих активаций: <b>{activations}</b>"
        )
    return (
        f"🎟 <b>{html.escape(promo.name or promo.code)}</b>\n\n"
        f"Код: <code>{html.escape(promo.code)}</code>\n"
        f"Статус: {'✅ ON' if promo.is_active else '⛔ OFF'}\n"
        f"Режим: скидка/бонус к покупке\n"
        f"Скидка %: {promo.discount_percent or '—'}\n"
        f"Скидка ₽: {promo.discount_fixed_rub or '—'}\n"
        f"Бесплатных дней: {promo.free_days}\n"
        f"+ запросов: {promo.additional_requests}\n"
        f"+ smart: {promo.additional_smart_requests}\n"
        f"Лимит/пользователь: {per_user}\n"
        f"Текущих активаций: {activations}\n"
        f"Начало: {_dt(promo.starts_at)}\n"
        f"Окончание: {_dt(promo.ends_at)}"
    )


async def _promo_detail_text(session: AsyncSession, promo: PromoCode) -> str:
    activations = await service.promo_activation_count(session, promo.id)
    plan = await session.get(Plan, promo.plan_id) if promo.plan_id else None
    return _promo_text(promo, activations=activations, plan_name=plan.name if plan else None)

def _broadcast_text(row: Broadcast) -> str:
    preview = html.escape(row.text[:900])
    return (
        f"📣 <b>Рассылка #{row.id}</b>\n"
        f"{html.escape(row.name)}\n\n"
        f"Статус: <b>{row.status}</b>\n"
        f"Аудитория: <code>{html.escape(str(row.filters or {}))}</code>\n"
        f"Всего: {row.total} · ✅ {row.sent} · ❌ {row.failed} · 🚫 {row.blocked}\n"
        f"Запланирована: {_dt(row.scheduled_at)}\n\n"
        f"<b>Текст:</b>\n{preview}"
    )


@router.callback_query(F.data.startswith("adm:"))
async def admin_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not _admin(settings, callback.from_user.id if callback.from_user else None):
        await _deny_callback(callback)
        return
    await callback.answer()
    actor = callback.from_user.id
    data = callback.data or ""
    parts = data.split(":")

    try:
        if data == "adm:main":
            await state.clear()
            await _edit_or_answer(callback, "⚙️ <b>Админ-панель</b>\n\nВыберите раздел.", admin_main_keyboard())
            return
        if data == "adm:more":
            await _edit_or_answer(callback, "⚙️ <b>Дополнительные настройки</b>", admin_more_keyboard())
            return
        if data == "adm:dashboard":
            await _edit_or_answer(callback, await _dashboard_text(session), back_keyboard())
            return
        if data == "adm:users":
            rows = await service.recent_users(session)
            await _edit_or_answer(callback, "👥 <b>Последние пользователи</b>\n\nМожно открыть карточку или выполнить поиск.", users_keyboard(rows))
            return
        if data == "adm:users:search":
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "user_search"})
            await _edit_or_answer(callback, "🔎 Отправьте Telegram ID, внутренний ID, username или имя.", back_keyboard("adm:users"))
            return
        if len(parts) == 3 and parts[1] == "user" and parts[2].isdigit():
            result = await _user_text(session, int(parts[2]))
            if result is None:
                raise TelegramAdminError("Пользователь не найден")
            text, user = result
            await _edit_or_answer(callback, text, user_keyboard(user.id, user.is_blocked))
            return
        if len(parts) >= 4 and parts[1] == "user":
            action = parts[2]
            user_id = int(parts[3])
            if action in {"addcredits", "removecredits", "message"}:
                await state.set_state(AdminInput.waiting)
                await state.set_data({"action": f"user_{action}", "user_id": user_id})
                prompt = {
                    "addcredits": "Введите количество кредитов, которое нужно начислить.",
                    "removecredits": "Введите количество кредитов, которое нужно списать. Баланс не может уйти в минус.",
                    "message": "Введите сообщение, которое бот отправит пользователю.",
                }[action]
                await _edit_or_answer(callback, prompt, back_keyboard(f"adm:user:{user_id}"))
                return
            if action == "block":
                await service.set_user_blocked(session, actor, user_id)
                await session.commit()
                result = await _user_text(session, user_id)
                assert result is not None
                text, user = result
                await _edit_or_answer(callback, text, user_keyboard(user.id, user.is_blocked))
                return
            if action == "trialreset":
                await service.reset_user_trial(session, actor, user_id)
                await session.commit()
                await _edit_or_answer(callback, "✅ Стартовый бонус сброшен. Пользователь снова увидит кнопку его получения.", back_keyboard(f"adm:user:{user_id}"))
                return
            if action == "grant":
                plans = await service.plans_list(session)
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=p.name, callback_data=f"adm:user:grantplan:{user_id}:{p.id}")]
                    for p in plans if p.is_active
                ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm:user:{user_id}")]])
                await _edit_or_answer(callback, "👑 Выберите тариф для выдачи/продления.", kb)
                return
            if action == "grantplan" and len(parts) >= 5:
                plan_id = int(parts[4])
                await service.grant_plan(session, actor, user_id, plan_id)
                await session.commit()
                await _edit_or_answer(callback, "✅ Тариф выдан/продлён.", back_keyboard(f"adm:user:{user_id}"))
                return

        if data == "adm:credits":
            packages = await service.credit_packages_list(session)
            await _edit_or_answer(callback, "💰 <b>Пакеты кредитов</b>\n\nОни показываются пользователю в разделе «Баланс».", credit_packages_keyboard(packages))
            return
        if data == "adm:credits:add":
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "credit_package_add"})
            await _edit_or_answer(callback, "Введите:\n<code>code | name | credits | bonus | RUB | Stars</code>\n\nПример: <code>credits_500 | 500 кредитов | 500 | 50 | 699 | 390</code>", back_keyboard("adm:credits"))
            return
        if len(parts) == 3 and parts[1] == "credits" and parts[2].isdigit():
            package = await session.get(CreditPackage, int(parts[2]))
            if package is None:
                raise TelegramAdminError("Пакет не найден")
            await _edit_or_answer(callback, _credit_package_text(package), credit_package_keyboard(package.id, package.is_active, package.is_recommended))
            return
        if len(parts) >= 4 and parts[1] == "credits":
            action = parts[2]; package_id = int(parts[3])
            if action in {"toggle", "recommend"}:
                package = await service.credit_package_toggle(session, actor, package_id, recommended=action == "recommend")
                await session.commit()
                await _edit_or_answer(callback, _credit_package_text(package), credit_package_keyboard(package.id, package.is_active, package.is_recommended))
                return
            if action == "edit" and len(parts) >= 5:
                await state.set_state(AdminInput.waiting)
                await state.set_data({"action": "credit_package_edit", "package_id": package_id, "field": parts[4]})
                await _edit_or_answer(callback, f"Введите новое значение для <code>{html.escape(parts[4])}</code>.", back_keyboard(f"adm:credits:{package_id}"))
                return

        if data == "adm:plans":
            plans = await service.plans_list(session)
            await _edit_or_answer(callback, "👑 <b>Тарифы</b>", plans_keyboard(plans))
            return
        if data == "adm:plan:add":
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "plan_add"})
            prompt = (
                "Введите:\n"
                "<code>code | name | RUB | Stars | days | requests | smart</code>\n\n"
                "Пример:\n"
                "<code>pro | Pro | 499 | 250 | 30 | 1500 | 50</code>\n"
                "Stars можно указать <code>-</code>. После создания модели тарифа можно изменить отдельными кнопками."
            )
            await _edit_or_answer(callback, prompt, back_keyboard("adm:plans"))
            return
        if len(parts) == 3 and parts[1] == "plan" and parts[2].isdigit():
            plan = await session.get(Plan, int(parts[2]))
            if plan is None:
                raise TelegramAdminError("Тариф не найден")
            await _edit_or_answer(callback, _plan_text(plan), plan_keyboard(plan.id, plan.is_active, plan.is_recommended))
            return
        if len(parts) >= 4 and parts[1] == "plan":
            action = parts[2]
            plan_id = int(parts[3])
            if action == "toggle":
                row = await service.toggle_plan(session, actor, plan_id)
                await session.commit()
                await _edit_or_answer(callback, _plan_text(row), plan_keyboard(row.id, row.is_active, row.is_recommended))
                return
            if action == "recommend":
                row = await service.toggle_plan_recommended(session, actor, plan_id)
                await session.commit()
                await _edit_or_answer(callback, _plan_text(row), plan_keyboard(row.id, row.is_active, row.is_recommended))
                return
            if action == "edit" and len(parts) >= 5:
                field = parts[4]
                await state.set_state(AdminInput.waiting)
                await state.set_data({"action": "plan_edit", "plan_id": plan_id, "field": field})
                await _edit_or_answer(callback, f"Введите новое значение для <code>{html.escape(field)}</code>.", back_keyboard(f"adm:plan:{plan_id}"))
                return
            if action == "delete":
                await service.plan_delete(session, actor, plan_id)
                await session.commit()
                await _edit_or_answer(callback, "✅ Тариф удалён.", back_keyboard("adm:plans"))
                return

        if data == "adm:trial":
            value = int(await service.get_setting(session, "credits.trial_bonus", 20) or 0)
            text = (
                "🎁 <b>Стартовый бонус</b>\n\n"
                f"Новый пользователь может один раз получить: <b>{value} кредитов</b>.\n"
                "После активации кнопка исчезает у этого пользователя."
            )
            await _edit_or_answer(callback, text, trial_keyboard())
            return

        if data == "adm:ai":
            values = await service.get_settings(session, ["ai.summary_model", "ai.history_messages", "ai.summary_trigger_messages", "ai.request_timeout_seconds"])
            modes = await service.ai_modes_list(session)
            text = (
                "🤖 <b>AI</b>\n\n"
                f"Режимов: <b>{len(modes)}</b>\n"
                f"Summary: <code>{html.escape(str(values.get('ai.summary_model') or settings.ai_summary_model))}</code>\n"
                f"History: {values.get('ai.history_messages') or settings.ai_history_messages}\n"
                f"Timeout: {values.get('ai.request_timeout_seconds') or settings.ai_request_timeout_seconds}s\n\n"
                "Названия реальных моделей видит только администратор."
            )
            await _edit_or_answer(callback, text, ai_keyboard())
            return
        if data == "adm:ai:modes":
            modes = await service.ai_modes_list(session)
            await _edit_or_answer(callback, "🎚 <b>Режимы AI</b>\n\nЗдесь задаются модель и стоимость одного запроса в кредитах.", ai_modes_keyboard(modes))
            return
        if len(parts) == 3 and parts[1] == "mode" and parts[2].isdigit():
            mode = await session.get(AIModelMode, int(parts[2]))
            if mode is None:
                raise TelegramAdminError("Режим не найден")
            await _edit_or_answer(callback, _mode_text(mode), ai_mode_keyboard(mode.id, mode.is_active))
            return
        if len(parts) >= 4 and parts[1] == "mode":
            action = parts[2]; mode_id = int(parts[3])
            if action == "toggle":
                mode = await service.ai_mode_toggle(session, actor, mode_id)
                await session.commit()
                await _edit_or_answer(callback, _mode_text(mode), ai_mode_keyboard(mode.id, mode.is_active))
                return
            if action == "edit" and len(parts) >= 5:
                await state.set_state(AdminInput.waiting)
                await state.set_data({"action": "mode_edit", "mode_id": mode_id, "field": parts[4]})
                await _edit_or_answer(callback, f"Введите новое значение для <code>{html.escape(parts[4])}</code>.", back_keyboard(f"adm:mode:{mode_id}"))
                return
        if data == "adm:ai:pricing":
            pricings = await service.pricing_list(session)
            await _edit_or_answer(callback, "💸 <b>Стоимость AI-моделей</b>\nUSD за 1M токенов.", pricing_keyboard(pricings))
            return
        if data == "adm:pricing:add":
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "pricing_add"})
            await _edit_or_answer(callback, "Введите:\n<code>model | input | cached | output</code>\n\nПример:\n<code>gpt-5.6-luna | 0.20 | 0.02 | 1.20</code>", back_keyboard("adm:ai:pricing"))
            return
        if len(parts) == 3 and parts[1] == "pricing" and parts[2].isdigit():
            row = await session.get(AIModelPricing, int(parts[2]))
            if row is None:
                raise TelegramAdminError("Модель не найдена")
            text = (
                f"💸 <b>{html.escape(row.model)}</b>\n\n"
                f"Input: ${row.input_price_per_million_usd}/1M\n"
                f"Cached: ${row.cached_input_price_per_million_usd}/1M\n"
                f"Output: ${row.output_price_per_million_usd}/1M\n"
                f"Active: {'✅' if row.is_active else '⛔'}"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Изменить цены", callback_data=f"adm:pricing:edit:{row.id}")],
                [InlineKeyboardButton(text="⬅️ Цены", callback_data="adm:ai:pricing")],
            ])
            await _edit_or_answer(callback, text, kb)
            return
        if len(parts) == 4 and parts[1] == "pricing" and parts[2] == "edit":
            pricing_id = int(parts[3])
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "pricing_edit", "pricing_id": pricing_id})
            await _edit_or_answer(callback, "Введите новые цены:\n<code>input | cached | output</code>", back_keyboard(f"adm:pricing:{pricing_id}"))
            return

        if data == "adm:payments":
            rows = await service.payments_list(session)
            lines = ["💳 <b>Последние платежи</b>", ""]
            for payment, user, plan, package in rows:
                who = f"@{user.username}" if user.username else str(user.telegram_id)
                product = package.name if package is not None else (plan.name if plan is not None else "—")
                lines.append(
                    f"#{payment.id} · <b>{payment.status}</b> · {html.escape(product)} · "
                    f"{payment.amount} {payment.currency} · {html.escape(who)} · {html.escape(payment.provider)}"
                )
            await _edit_or_answer(callback, "\n".join(lines) if rows else "💳 Платежей пока нет.", back_keyboard())
            return

        if data == "adm:providers":
            rows = await service.providers_list(session)
            await _edit_or_answer(callback, "🔌 <b>Платёжные системы</b>\n\nСекреты по-прежнему хранятся только в .env.", providers_keyboard(rows))
            return
        if len(parts) == 3 and parts[1] == "provider" and parts[2].isdigit():
            row = await session.get(PaymentProviderSetting, int(parts[2]))
            if row is None:
                raise TelegramAdminError("Провайдер не найден")
            configured = payment_provider_configured(settings, row.provider)
            callback_url = (
                None
                if row.provider == "telegram_stars"
                else f"{settings.public_base_url.rstrip('/')}/api/webhooks/payments/{row.provider}"
            )
            text = (
                f"🔌 <b>{html.escape(row.display_name)}</b>\n\n"
                f"Название для пользователей: <b>{html.escape(row.display_name)}</b>\n"
                f"Статус: {'✅ включён' if row.enabled else '⛔ выключен'}\n"
                f"Конфигурация .env: {'✅ готова' if configured else '⚠️ не заполнена'}\n"
                f"Test mode: {'🧪 ON' if row.test_mode else 'OFF'}\n"
                f"Комиссия: {row.fee_percent}% + {row.fee_fixed_rub} ₽\n"
                f"Системный код: <code>{row.provider}</code>\n\n"
                "ℹ️ Название можно менять как угодно — пользователь увидит его на кнопке выбора оплаты. "
                "Системный код и работа платёжного провайдера при этом не меняются."
                + (
                    f"\n\nWebhook/Callback URL:\n<code>{html.escape(callback_url)}</code>"
                    if callback_url
                    else ""
                )
            )
            await _edit_or_answer(callback, text, provider_keyboard(row.id, row.enabled, row.test_mode))
            return
        if len(parts) >= 4 and parts[1] == "provider":
            action = parts[2]
            provider_id = int(parts[3])
            if action in {"toggle", "test"}:
                row = await service.provider_toggle(session, actor, provider_id, "enabled" if action == "toggle" else "test_mode")
                await session.commit()
                await _edit_or_answer(callback, f"✅ {html.escape(row.display_name)} обновлён.", back_keyboard(f"adm:provider:{provider_id}"))
                return
            if action == "edit" and len(parts) >= 5:
                field = parts[4]
                await state.set_state(AdminInput.waiting)
                await state.set_data({"action": "provider_edit", "provider_id": provider_id, "field": field})
                if field == "display_name":
                    prompt = (
                        "✏️ <b>Название для пользователей</b>\n\n"
                        "Введите название, которое будет показано на кнопке оплаты.\n"
                        "Например: <code>ТГ звёздочки</code> или <code>Оплата картой</code>.\n\n"
                        "До 40 символов. Системное название провайдера не изменится."
                    )
                elif field == "fee_percent":
                    prompt = "Введите комиссию в процентах. Например: <code>3.5</code>."
                elif field == "fee_fixed_rub":
                    prompt = "Введите фиксированную комиссию в рублях. Например: <code>0</code>."
                else:
                    raise TelegramAdminError("Недопустимое поле")
                await _edit_or_answer(callback, prompt, back_keyboard(f"adm:provider:{provider_id}"))
                return

        if data == "adm:promos":
            await state.clear()
            promos = await service.promos_list(session)
            await _edit_or_answer(callback, "🎟 <b>Промокоды</b>\n\nПромокод на кредиты сразу пополняет баланс после активации. Скидочный промокод оставлен для совместимости со старыми покупками.", promos_keyboard(promos))
            return
        if data == "adm:promo:add":
            # Use the same generic admin input state that package/provider/settings editing already
            # uses reliably. The older dedicated PromoCreate state is still handled below only so
            # an administrator who had an unfinished wizard before deploy is not stranded.
            await state.clear()
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "promo_create_name"})
            await _edit_or_answer(
                callback,
                "🎟 <b>Новый промокод</b>\n\n1/5. Введите название.\nНапример: <code>Подарок 50 кредитов</code>",
                back_keyboard("adm:promos"),
            )
            return
        if data == "adm:promo:addpurchase":
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "promo_add"})
            await _edit_or_answer(callback, "💸 <b>Скидка / бонус к покупке</b> — legacy-режим.\n\nВведите одной строкой:\n<code>CODE | % | ₽ | дни | запросы | smart | plan_code(optional)</code>\n\nДля обычных раздач используйте «Промокод на кредиты».", back_keyboard("adm:promos"))
            return
        if data == "adm:promo:create:confirm":
            values = await state.get_data()
            required = {"name", "code", "credits", "max_activations", "per_user_limit"}
            if not required.issubset(values):
                raise TelegramAdminError("Данные мастера устарели. Создайте промокод заново.")
            row = await service.promo_create_credits(
                session,
                actor,
                name=str(values["name"]),
                code=str(values["code"]),
                credits=int(values["credits"]),
                max_activations=int(values["max_activations"]),
                per_user_limit=int(values["per_user_limit"]),
            )
            await session.commit()
            await state.clear()
            await _edit_or_answer(
                callback,
                "✅ <b>Промокод создан и уже включён</b>\n\n" + await _promo_detail_text(session, row),
                promo_keyboard(row.id, row.is_active),
            )
            return
        if len(parts) == 3 and parts[1] == "promo" and parts[2].isdigit():
            row = await session.get(PromoCode, int(parts[2]))
            if row is None:
                raise TelegramAdminError("Промокод не найден")
            await _edit_or_answer(callback, await _promo_detail_text(session, row), promo_keyboard(row.id, row.is_active))
            return
        if len(parts) == 4 and parts[1] == "promo" and parts[2] == "toggle":
            row = await service.promo_toggle(session, actor, int(parts[3]))
            await session.commit()
            await _edit_or_answer(callback, await _promo_detail_text(session, row), promo_keyboard(row.id, row.is_active))
            return
        if len(parts) == 4 and parts[1] == "promo" and parts[2] == "validity":
            promo_id = int(parts[3])
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "promo_validity", "promo_id": promo_id})
            await _edit_or_answer(
                callback,
                "⏳ Введите срок действия промокода в днях.\n<code>-1</code> — без ограничения. Например: <code>7</code>.",
                back_keyboard(f"adm:promo:{promo_id}"),
            )
            return

        if len(parts) == 5 and parts[1] == "promo" and parts[2] == "edit":
            promo_id = int(parts[3])
            field = parts[4]
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "promo_edit", "promo_id": promo_id, "field": field})
            hints = {
                "name": "Введите новое название.",
                "code": "Введите новый код промокода.",
                "additional_credits": "Введите количество кредитов (1–10 000 000).",
                "free_days": "Введите количество дней (legacy).",
                "max_activations": "Введите общее количество активаций. -1 = безлимитно.",
                "per_user_limit": "Сколько раз один пользователь может активировать код? -1 = безлимитно.",
            }
            await _edit_or_answer(callback, hints.get(field, "Введите новое значение."), back_keyboard(f"adm:promo:{promo_id}"))
            return
        if len(parts) == 4 and parts[1] == "promo" and parts[2] == "scope":
            promo_id = int(parts[3])
            row = await session.get(PromoCode, promo_id)
            if row is None:
                raise TelegramAdminError("Промокод не найден")
            await _edit_or_answer(callback, "👥 <b>Тип подписки</b>\n\nДля всех — любой пользователь.\nТолько первая — у пользователя ещё не было подписки.\nНе первая — подписка уже была.", promo_scope_keyboard(prefix=f"adm:promo:setscope:{promo_id}", current=row.subscription_scope))
            return
        if len(parts) == 5 and parts[1] == "promo" and parts[2] == "setscope":
            promo_id = int(parts[3]); scope = parts[4]
            row = await service.promo_set_scope(session, actor, promo_id, scope)
            await session.commit()
            await _edit_or_answer(callback, await _promo_detail_text(session, row), promo_keyboard(row.id, row.is_active))
            return
        if len(parts) == 4 and parts[1] == "promo" and parts[2] == "plan":
            promo_id = int(parts[3])
            plans = [p for p in await service.plans_list(session) if p.is_active]
            await _edit_or_answer(callback, "👑 Выберите тариф промокода.", promo_plan_keyboard(plans, prefix=f"adm:promo:setplan:{promo_id}", back=f"adm:promo:{promo_id}"))
            return
        if len(parts) == 5 and parts[1] == "promo" and parts[2] == "setplan":
            promo_id = int(parts[3]); plan_id = int(parts[4])
            row = await service.promo_set_plan(session, actor, promo_id, plan_id)
            await session.commit()
            await _edit_or_answer(callback, await _promo_detail_text(session, row), promo_keyboard(row.id, row.is_active))
            return

        if data == "adm:referrals":
            keys = [
                "referral.enabled",
                "referral.level2_enabled",
                "referral.registration_bonus_credits",
                "referral.first_payment_bonus_credits",
                "referral.level2_registration_bonus_credits",
                "referral.level2_first_payment_bonus_credits",
                "referral.paying_friends_target",
                "referral.milestone_reward_credits",
            ]
            values = await service.get_settings(session, keys)
            total, paid = await service.referral_counts(session)
            enabled = bool(values.get("referral.enabled"))
            level2_enabled = bool(values.get("referral.level2_enabled"))
            text = (
                "🎁 <b>Реферальная система</b>\n\n"
                f"Статус: {'✅ ON' if enabled else '⛔ OFF'}\n"
                f"2-й уровень: {'✅ ON' if level2_enabled else '⚪ OFF'}\n"
                f"Всего реферальных связей: {total}\n"
                f"С первой покупкой: {paid}\n\n"
                "<b>1-й уровень — приглашённые пользователем</b>\n"
                f"Регистрация: +{values.get('referral.registration_bonus_credits') or 0} кредитов\n"
                f"Первая покупка: +{values.get('referral.first_payment_bonus_credits') or 0} кредитов\n\n"
                "<b>2-й уровень — друзья его друзей</b>\n"
                f"Регистрация: +{values.get('referral.level2_registration_bonus_credits') or 0} кредитов\n"
                f"Первая покупка: +{values.get('referral.level2_first_payment_bonus_credits') or 0} кредитов\n\n"
                "<b>Бонус за активных друзей 1-го уровня</b>\n"
                f"Каждые {values.get('referral.paying_friends_target') or 0}: "
                f"+{values.get('referral.milestone_reward_credits') or 0} кредитов\n\n"
                "ℹ️ Значение 0 отключает конкретную награду. "
                "Существующий пользователь не может привязаться к новому рефереру задним числом."
            )
            await _edit_or_answer(
                callback, text, referrals_keyboard(enabled, level2_enabled)
            )
            return
        if data == "adm:ref:toggle":
            value = await service.toggle_setting(session, actor, "referral.enabled", True)
            await session.commit()
            await _edit_or_answer(
                callback,
                f"✅ Реферальная система {'включена' if value else 'выключена'}.",
                back_keyboard("adm:referrals"),
            )
            return
        if data == "adm:ref:level2:toggle":
            value = await service.toggle_setting(
                session, actor, "referral.level2_enabled", True
            )
            await session.commit()
            await _edit_or_answer(
                callback,
                f"✅ Второй уровень {'включён' if value else 'выключен'}.",
                back_keyboard("adm:referrals"),
            )
            return

        if data == "adm:broadcasts":
            rows = await service.broadcasts_list(session)
            await _edit_or_answer(callback, "📣 <b>Рассылки</b>\n\nСоздание, запуск и остановка доступны прямо здесь.", broadcasts_keyboard(rows))
            return
        if data == "adm:broadcast:add":
            await state.set_state(BroadcastDraft.content)
            await state.set_data({})
            await _edit_or_answer(callback, "📣 Отправьте текст рассылки.\n\nМожно также прислать <b>фото с подписью</b> — Telegram file_id будет сохранён и использован worker'ом.", back_keyboard("adm:broadcasts"))
            return
        if len(parts) == 3 and parts[1] == "broadcast" and parts[2].isdigit():
            row = await session.get(Broadcast, int(parts[2]))
            if row is None:
                raise TelegramAdminError("Рассылка не найдена")
            await _edit_or_answer(callback, _broadcast_text(row), broadcast_keyboard(row.id, row.status))
            return
        if len(parts) == 4 and parts[1] == "broadcast" and parts[2] in {"schedule", "buttons"}:
            bid = int(parts[3])
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": f"broadcast_{parts[2]}", "broadcast_id": bid})
            if parts[2] == "schedule":
                prompt = "Введите UTC дату/время: <code>YYYY-MM-DD HH:MM</code>"
            else:
                prompt = (
                    "Введите URL-кнопки, по одной на строку:\n"
                    "<code>Текст | https://example.com</code>\n\n"
                    "Чтобы удалить все кнопки, отправьте <code>-</code>."
                )
            await _edit_or_answer(callback, prompt, back_keyboard(f"adm:broadcast:{bid}"))
            return

        if len(parts) == 4 and parts[1] == "broadcast" and parts[2] in {"start", "stop"}:
            bid = int(parts[3])
            if parts[2] == "start":
                row = await service.broadcast_start(session, actor, bid)
                await session.commit()
                from app.workers.tasks import execute_broadcast_task
                execute_broadcast_task.delay(row.id)
                await _edit_or_answer(callback, "▶️ Рассылка поставлена в очередь.", back_keyboard(f"adm:broadcast:{bid}"))
            else:
                await service.broadcast_stop(session, actor, bid)
                await session.commit()
                await _edit_or_answer(callback, "⏹ Запрошена остановка рассылки.", back_keyboard(f"adm:broadcast:{bid}"))
            return
        if len(parts) == 4 and parts[1] == "broadcast" and parts[2] == "aud":
            current = await state.get_data()
            if not current.get("broadcast_text"):
                raise TelegramAdminError("Черновик рассылки потерян. Создайте его заново.")
            row = await service.broadcast_create(
                session,
                actor,
                text=str(current["broadcast_text"]),
                telegram_file_id=current.get("telegram_file_id"),
                audience=parts[3],
            )
            await session.commit()
            await state.clear()
            await _edit_or_answer(callback, "✅ Рассылка создана как черновик.\n\n" + _broadcast_text(row), broadcast_keyboard(row.id, row.status))
            return

        if data == "adm:notifications":
            rows = await service.notification_rows(session)
            await _edit_or_answer(callback, "🔔 <b>Уведомления администраторам</b>\n\nПолучатели могут быть автоматически созданы из ADMIN_TELEGRAM_IDS.", notifications_keyboard(rows))
            return
        if data == "adm:notif:subscription":
            from app.notifications.config import SubscriptionNotificationConfigRepository
            config = await SubscriptionNotificationConfigRepository().load(session)
            text = (
                "⏳ <b>Напоминания подписки</b>\n\n"
                f"Enabled: {'✅' if config.enabled else '⛔'}\n"
                f"До окончания: {', '.join(map(str, config.days_before)) or '—'} дн.\n"
                f"В день окончания: {'✅' if config.expiry_day else '⛔'}\n"
                f"В момент окончания: {'✅' if config.at_expiry else '⛔'}\n"
                f"После окончания: {', '.join(map(str, config.days_after)) or '—'} дн."
            )
            await _edit_or_answer(callback, text, subscription_notifications_keyboard(config))
            return
        if data == "adm:notif:templates":
            await _edit_or_answer(callback, "📝 <b>Шаблоны напоминаний</b>\n\nДоступны: <code>{plan_name}</code>, <code>{days}</code>, <code>{expires_date}</code>, <code>{expires_datetime}</code>.", notification_templates_keyboard())
            return
        if len(parts) == 4 and parts[1] == "notif" and parts[2] == "subtoggle":
            mapping = {
                "enabled": "notifications.subscription.enabled",
                "expiry_day": "notifications.subscription.expiry_day",
                "at_expiry": "notifications.subscription.at_expiry",
            }
            key = mapping.get(parts[3])
            if key is None:
                raise TelegramAdminError("Неизвестная настройка")
            value = await service.toggle_setting(session, actor, key, True)
            await session.commit()
            await _edit_or_answer(callback, f"✅ {key}: {'ON' if value else 'OFF'}", back_keyboard("adm:notif:subscription"))
            return

        if data == "adm:notif:sync":
            count = await service.sync_notification_admins(session, actor, settings)
            await session.commit()
            await _edit_or_answer(callback, f"✅ Синхронизация завершена. Добавлено: {count}.", back_keyboard("adm:notifications"))
            return
        if len(parts) == 3 and parts[1] == "notif" and parts[2].isdigit():
            from app.db.models import AdminNotificationSetting
            row = await session.get(AdminNotificationSetting, int(parts[2]))
            if row is None:
                raise TelegramAdminError("Получатель не найден")
            await _edit_or_answer(callback, f"🔔 <b>Получатель {row.telegram_id}</b>\n\nВключите нужные категории.", notification_keyboard(row))
            return
        if len(parts) >= 4 and parts[1] == "notif":
            if parts[2] == "enabled":
                row = await service.notification_toggle(session, actor, int(parts[3]), "enabled")
                await session.commit()
                await _edit_or_answer(callback, f"✅ Получатель {'включён' if row.enabled else 'выключен'}.", back_keyboard(f"adm:notif:{row.id}"))
                return
            if parts[2] == "field" and len(parts) >= 5:
                row = await service.notification_toggle(session, actor, int(parts[3]), parts[4])
                await session.commit()
                await _edit_or_answer(callback, "✅ Настройка обновлена.", back_keyboard(f"adm:notif:{row.id}"))
                return

        if data == "adm:settings":
            values = await service.get_settings(session, ["service.name", "service.bot_username", "service.support_username", "service.maintenance_mode", "economics.usd_to_rub", "broadcasts.messages_per_second"])
            maintenance = bool(values.get("service.maintenance_mode"))
            text = (
                "⚙️ <b>Настройки</b>\n\n"
                f"Сервис: {html.escape(str(values.get('service.name') or settings.app_name))}\n"
                f"Bot username: {html.escape(str(values.get('service.bot_username') or '—'))}\n"
                f"Support: {html.escape(str(values.get('service.support_username') or settings.support_username or '—'))}\n"
                f"Maintenance: {'🛠 ON' if maintenance else '🟢 OFF'}\n"
                f"USD/RUB: {values.get('economics.usd_to_rub') or 0}\n"
                f"Рассылка/с: {values.get('broadcasts.messages_per_second') or 25}"
            )
            await _edit_or_answer(callback, text, settings_keyboard(maintenance))
            return
        if data == "adm:settings:maintenance":
            value = await service.toggle_setting(session, actor, "service.maintenance_mode", False)
            await session.commit()
            await _edit_or_answer(callback, f"✅ Maintenance {'включён' if value else 'выключен'}.", back_keyboard("adm:settings"))
            return

        if data == "adm:legal":
            agreement = await _legal_config(session, settings, "agreement")
            privacy = await _legal_config(session, settings, "privacy")
            text = (
                "📄 <b>Соглашение и политика</b>\n\n"
                "Эти кнопки показываются в главном меню пользователя. "
                "Для каждой можно отдельно изменить текст, ссылку и видимость.\n\n"
                f"{'🟢' if agreement['enabled'] else '🔴'} {html.escape(str(agreement['text']))}\n"
                f"{'🟢' if privacy['enabled'] else '🔴'} {html.escape(str(privacy['text']))}"
            )
            await _edit_or_answer(
                callback,
                text,
                legal_buttons_keyboard(
                    agreement_enabled=bool(agreement["enabled"]),
                    privacy_enabled=bool(privacy["enabled"]),
                ),
            )
            return

        if len(parts) == 3 and parts[1] == "legal" and parts[2] in LEGAL_BUTTONS:
            config = await _legal_config(session, settings, parts[2])
            await _edit_or_answer(
                callback,
                _legal_detail_text(config),
                legal_button_keyboard(parts[2], enabled=bool(config["enabled"])),
            )
            return

        if len(parts) == 4 and parts[1] == "legal" and parts[2] in LEGAL_BUTTONS:
            kind = parts[2]
            action = parts[3]
            item = LEGAL_BUTTONS[kind]
            if action == "toggle":
                config = await _legal_config(session, settings, kind)
                if not bool(config["enabled"]) and not str(config["url"] or "").strip():
                    raise TelegramAdminError("Сначала укажите ссылку, затем включите кнопку")
                await service.toggle_setting(
                    session,
                    actor,
                    str(item["enabled_key"]),
                    bool(_legal_defaults(kind, settings)[0]),
                )
                await session.commit()
                config = await _legal_config(session, settings, kind)
                await _edit_or_answer(
                    callback,
                    _legal_detail_text(config),
                    legal_button_keyboard(kind, enabled=bool(config["enabled"])),
                )
                return
            if action in {"text", "url"}:
                key = str(item["text_key"] if action == "text" else item["url_key"])
                kind_name = "button_text" if action == "text" else "url"
                prompt = (
                    "Введите новый текст кнопки (до 64 символов)."
                    if action == "text"
                    else "Введите новую ссылку целиком, начиная с https:// или http://.\nЧтобы очистить ссылку, отправьте <code>-</code>."
                )
                await state.set_state(AdminInput.waiting)
                await state.set_data({
                    "action": "setting_edit",
                    "key": key,
                    "kind": kind_name,
                    "back": f"adm:legal:{kind}",
                })
                await _edit_or_answer(callback, prompt, back_keyboard(f"adm:legal:{kind}"))
                return

        if len(parts) == 3 and parts[1] == "tpl":
            mapping = {
                "before": "notifications.subscription.template_before",
                "expiryday": "notifications.subscription.template_expiry_day",
                "expired": "notifications.subscription.template_expired",
                "after": "notifications.subscription.template_after",
            }
            key = mapping.get(parts[2])
            if key is None:
                raise TelegramAdminError("Неизвестный шаблон")
            await state.set_state(AdminInput.waiting)
            await state.set_data({"action": "setting_edit", "key": key, "kind": "template"})
            await _edit_or_answer(callback, f"Отправьте новый шаблон для <code>{html.escape(key)}</code>.", back_keyboard("adm:notif:templates"))
            return

        if len(parts) == 4 and parts[1] == "setting":
            key = parts[2]
            kind = parts[3]
            back_target = "adm:referrals" if key.startswith("referral.") else "adm:settings"
            await state.set_state(AdminInput.waiting)
            await state.set_data(
                {"action": "setting_edit", "key": key, "kind": kind, "back": back_target}
            )
            await _edit_or_answer(
                callback,
                f"Введите новое значение для <code>{html.escape(key)}</code>.",
                back_keyboard(back_target),
            )
            return

        if data == "adm:errors":
            rows = await service.errors_list(session)
            lines = ["🚨 <b>Последние ошибки</b>", ""]
            keyboard: list[list[InlineKeyboardButton]] = []
            for row in rows:
                icon = "✅" if row.resolved else "🚨"
                lines.append(f"{icon} #{row.id} {html.escape(row.service)}/{html.escape(row.category)} ×{row.occurrence_count}\n{html.escape(row.message[:160])}")
                if not row.resolved:
                    keyboard.append([InlineKeyboardButton(text=f"✅ Закрыть #{row.id}", callback_data=f"adm:error:resolve:{row.id}")])
            keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:more")])
            await _edit_or_answer(callback, "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard))
            return
        if len(parts) == 4 and parts[1] == "error" and parts[2] == "resolve":
            await service.resolve_error(session, actor, int(parts[3]))
            await session.commit()
            await _edit_or_answer(callback, "✅ Ошибка помечена решённой.", back_keyboard("adm:errors"))
            return

        if data == "adm:audit":
            rows = await service.audit_list(session)
            lines = ["🧾 <b>Последние действия</b>", ""]
            for row in rows:
                actor_id = (row.details or {}).get("actor_telegram_id", "web/legacy")
                lines.append(f"#{row.id} · {html.escape(row.action)} · actor {actor_id} · {_dt(row.created_at)}")
            await _edit_or_answer(callback, "\n".join(lines), back_keyboard("adm:more"))
            return

        await _edit_or_answer(callback, "Раздел не найден.", back_keyboard())
    except TelegramAdminError as exc:
        await session.rollback()
        await _edit_or_answer(callback, f"❌ {html.escape(str(exc))}", back_keyboard())
    except Exception as exc:
        await session.rollback()
        await state.clear()
        logger.exception("Unexpected Telegram admin callback error")
        await _edit_or_answer(
            callback,
            "❌ Произошла внутренняя ошибка. Текущее действие отменено — откройте раздел заново.",
            back_keyboard(),
        )
        raise


@router.message(StateFilter(
    PromoCreate.name, PromoCreate.code, PromoCreate.credits, PromoCreate.scope, PromoCreate.plan,
    PromoCreate.days, PromoCreate.max_activations, PromoCreate.per_user_limit, PromoCreate.confirm,
))
async def promo_create_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not _admin(settings, message.from_user.id if message.from_user else None):
        await state.clear()
        await _deny_message(message)
        return
    current = await state.get_state()
    raw = (message.text or "").strip()
    try:
        if current == PromoCreate.name.state:
            if not raw or len(raw) > 128:
                raise TelegramAdminError("Название должно содержать 1–128 символов")
            await state.update_data(name=raw)
            await state.set_state(PromoCreate.code)
            await message.answer("2/5. Введите <b>промокод</b>.\nНапример: <code>GIFT50</code>", reply_markup=back_keyboard("adm:promos"))
            return
        if current == PromoCreate.code.state:
            code = raw.upper()[:64]
            if not code:
                raise TelegramAdminError("Код не может быть пустым")
            if any(ch.isspace() for ch in code):
                raise TelegramAdminError("В промокоде не должно быть пробелов")
            if await session.scalar(select(PromoCode.id).where(PromoCode.code == code)) is not None:
                raise TelegramAdminError("Такой промокод уже существует")
            await state.update_data(code=code)
            await state.set_state(PromoCreate.credits)
            await message.answer("3/5. Сколько <b>кредитов</b> начислить после активации?\nНапример: <code>50</code>", reply_markup=back_keyboard("adm:promos"))
            return
        if current == PromoCreate.credits.state:
            credits = int(raw)
            if credits <= 0 or credits > 10_000_000:
                raise TelegramAdminError("Введите от 1 до 10 000 000 кредитов")
            await state.update_data(credits=credits)
            await state.set_state(PromoCreate.max_activations)
            await message.answer("4/5. Общее количество активаций?\n<code>-1</code> — без ограничений.", reply_markup=back_keyboard("adm:promos"))
            return
        if current == PromoCreate.max_activations.state:
            value = int(raw)
            if value != -1 and value <= 0:
                raise TelegramAdminError("Введите -1 или число больше 0")
            await state.update_data(max_activations=value)
            await state.set_state(PromoCreate.per_user_limit)
            await message.answer("5/5. Сколько раз один пользователь может активировать код?\n<code>1</code> — обычно лучший вариант. <code>-1</code> — без ограничений.", reply_markup=back_keyboard("adm:promos"))
            return
        if current == PromoCreate.per_user_limit.state:
            value = int(raw)
            if value != -1 and value <= 0:
                raise TelegramAdminError("Введите -1 или число больше 0")
            await state.update_data(per_user_limit=value)
            values = await state.get_data()
            max_text = "∞" if int(values["max_activations"]) == -1 else str(values["max_activations"])
            per_text = "∞" if value == -1 else str(value)
            summary = (
                "🎟 <b>Проверьте промокод</b>\n\n"
                f"Название: <b>{html.escape(str(values['name']))}</b>\n"
                f"Код: <code>{html.escape(str(values['code']))}</code>\n"
                f"Награда: <b>+{values['credits']} кредитов</b>\n"
                f"Активаций всего: <b>{max_text}</b>\n"
                f"На пользователя: <b>{per_text}</b>"
            )
            await state.set_state(PromoCreate.confirm)
            await message.answer(summary, reply_markup=promo_confirm_keyboard())
            return
        await message.answer("Используйте кнопки под сообщением или вернитесь в /admin.", reply_markup=back_keyboard("adm:promos"))
    except (TelegramAdminError, ValueError) as exc:
        await message.answer(f"❌ {html.escape(str(exc))}\n\nПопробуйте ещё раз.")
    except Exception as exc:
        await session.rollback()
        await state.clear()
        logger.exception("Unexpected promo wizard error")
        await message.answer(
            "❌ Произошла внутренняя ошибка. Мастер создания промокода сброшен.\n\n"
            "Откройте «🎟 Промокоды» и попробуйте ещё раз."
        )
        raise


@router.message(StateFilter(AdminInput.waiting))
async def admin_input_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
) -> None:
    if not _admin(settings, message.from_user.id if message.from_user else None):
        await state.clear()
        await _deny_message(message)
        return
    actor = message.from_user.id
    state_data = await state.get_data()
    action = str(state_data.get("action") or "")
    raw = message.text or ""
    try:
        if action == "promo_create_name":
            value = raw.strip()
            if not value or len(value) > 128:
                raise TelegramAdminError("Название должно содержать 1–128 символов")
            await state.update_data(name=value, action="promo_create_code")
            await message.answer(
                "2/5. Введите промокод.\nНапример: <code>GIFT50</code>",
                reply_markup=back_keyboard("adm:promos"),
            )
            return
        if action == "promo_create_code":
            code = raw.strip().upper()[:64]
            if not code:
                raise TelegramAdminError("Код не может быть пустым")
            if any(ch.isspace() for ch in code):
                raise TelegramAdminError("В промокоде не должно быть пробелов")
            if await session.scalar(select(PromoCode.id).where(PromoCode.code == code)) is not None:
                raise TelegramAdminError("Такой промокод уже существует")
            await state.update_data(code=code, action="promo_create_credits")
            await message.answer(
                "3/5. Сколько кредитов начислять после активации?\nНапример: <code>50</code>",
                reply_markup=back_keyboard("adm:promos"),
            )
            return
        if action == "promo_create_credits":
            credits_value = int(raw.strip())
            if credits_value <= 0 or credits_value > 10_000_000:
                raise TelegramAdminError("Введите от 1 до 10 000 000 кредитов")
            await state.update_data(credits=credits_value, action="promo_create_max")
            await message.answer(
                "4/5. Сколько активаций всего?\n<code>-1</code> — без ограничений.",
                reply_markup=back_keyboard("adm:promos"),
            )
            return
        if action == "promo_create_max":
            max_value = int(raw.strip())
            if max_value != -1 and max_value <= 0:
                raise TelegramAdminError("Введите -1 или число больше 0")
            await state.update_data(max_activations=max_value, action="promo_create_per_user")
            await message.answer(
                "5/5. Сколько раз один пользователь может активировать код?\n"
                "Обычно: <code>1</code>. <code>-1</code> — без ограничений.",
                reply_markup=back_keyboard("adm:promos"),
            )
            return
        if action == "promo_create_per_user":
            per_value = int(raw.strip())
            if per_value != -1 and per_value <= 0:
                raise TelegramAdminError("Введите -1 или число больше 0")
            await state.update_data(per_user_limit=per_value, action="promo_create_confirm")
            values = await state.get_data()
            max_text = "∞" if int(values["max_activations"]) == -1 else str(values["max_activations"])
            per_text = "∞" if per_value == -1 else str(per_value)
            await message.answer(
                "🎟 <b>Проверьте промокод</b>\n\n"
                f"Название: <b>{html.escape(str(values['name']))}</b>\n"
                f"Код: <code>{html.escape(str(values['code']))}</code>\n"
                f"Награда: <b>+{int(values['credits'])} кредитов</b>\n"
                f"Активаций всего: <b>{max_text}</b>\n"
                f"На пользователя: <b>{per_text}</b>",
                reply_markup=promo_confirm_keyboard(),
            )
            return
        if action == "promo_create_confirm":
            await message.answer(
                "Нажмите «✅ Создать промокод» под предыдущим сообщением или вернитесь назад.",
                reply_markup=back_keyboard("adm:promos"),
            )
            return
        if action == "user_search":
            rows = await service.search_users(session, raw)
            await state.clear()
            await message.answer(
                f"🔎 Найдено: {len(rows)}",
                reply_markup=users_keyboard(rows),
            )
            return
        if action == "user_addreq":
            user_id = int(state_data["user_id"])
            await service.add_user_requests(session, actor, user_id, int(raw))
            await session.commit()
            await state.clear()
            await message.answer("✅ Запросы добавлены.", reply_markup=back_keyboard(f"adm:user:{user_id}"))
            return
        if action == "user_adddays":
            user_id = int(state_data["user_id"])
            await service.add_user_days(session, actor, user_id, int(raw))
            await session.commit()
            await state.clear()
            await message.answer("✅ Дни добавлены.", reply_markup=back_keyboard(f"adm:user:{user_id}"))
            return
        if action == "user_addcredits":
            user_id = int(state_data["user_id"])
            wallet = await service.add_user_credits(session, actor, user_id, int(raw))
            await session.commit(); await state.clear()
            await message.answer(f"✅ Кредиты начислены. Баланс: <b>{wallet.balance}</b>.", reply_markup=back_keyboard(f"adm:user:{user_id}"))
            return
        if action == "user_removecredits":
            user_id = int(state_data["user_id"])
            wallet = await service.remove_user_credits(session, actor, user_id, int(raw))
            await session.commit(); await state.clear()
            await message.answer(f"✅ Кредиты списаны. Баланс: <b>{wallet.balance}</b>.", reply_markup=back_keyboard(f"adm:user:{user_id}"))
            return
        if action == "credit_package_add":
            row = await service.credit_package_create(session, actor, raw)
            await session.commit(); await state.clear()
            await message.answer("✅ Пакет создан.\n\n" + _credit_package_text(row), reply_markup=credit_package_keyboard(row.id, row.is_active, row.is_recommended))
            return
        if action == "credit_package_edit":
            row = await service.credit_package_edit(session, actor, int(state_data["package_id"]), str(state_data["field"]), raw)
            await session.commit(); await state.clear()
            await message.answer("✅ Пакет обновлён.\n\n" + _credit_package_text(row), reply_markup=credit_package_keyboard(row.id, row.is_active, row.is_recommended))
            return
        if action == "mode_edit":
            row = await service.ai_mode_edit(session, actor, int(state_data["mode_id"]), str(state_data["field"]), raw)
            await session.commit(); await state.clear()
            await message.answer("✅ Режим обновлён.\n\n" + _mode_text(row), reply_markup=ai_mode_keyboard(row.id, row.is_active))
            return
        if action == "user_message":
            user_id = int(state_data["user_id"])
            result = await service.send_direct_message(session, bot, actor, user_id, raw)
            await session.commit()
            await state.clear()
            await message.answer(
                "✅ Сообщение отправлено." if result.status == "sent" else f"❌ Не отправлено: {html.escape(result.error or 'unknown')}",
                reply_markup=back_keyboard(f"adm:user:{user_id}"),
            )
            return
        if action == "plan_add":
            row = await service.plan_create(session, actor, raw)
            await session.commit()
            await state.clear()
            await message.answer("✅ Тариф создан.\n\n" + _plan_text(row), reply_markup=plan_keyboard(row.id, row.is_active, row.is_recommended))
            return
        if action == "broadcast_schedule":
            bid = int(state_data["broadcast_id"])
            row = await service.broadcast_schedule(session, actor, bid, raw)
            await session.commit()
            await state.clear()
            await message.answer(f"✅ Рассылка запланирована на {_dt(row.scheduled_at)} UTC.", reply_markup=back_keyboard(f"adm:broadcast:{bid}"))
            return
        if action == "broadcast_buttons":
            bid = int(state_data["broadcast_id"])
            buttons_raw = "" if raw.strip() == "-" else raw
            row = await service.broadcast_set_buttons(session, actor, bid, buttons_raw)
            await session.commit()
            await state.clear()
            await message.answer(f"✅ URL-кнопки сохранены: {len(row.buttons)}.", reply_markup=back_keyboard(f"adm:broadcast:{bid}"))
            return
        if action == "plan_edit":
            plan_id = int(state_data["plan_id"])
            row = await service.edit_plan_field(session, actor, plan_id, str(state_data["field"]), raw)
            await session.commit()
            await state.clear()
            await message.answer("✅ Тариф обновлён.\n\n" + _plan_text(row), reply_markup=plan_keyboard(row.id, row.is_active, row.is_recommended))
            return
        if action == "setting_edit":
            await service.set_setting(session, actor, str(state_data["key"]), raw, str(state_data["kind"]))
            await session.commit()
            back_target = str(state_data.get("back") or "adm:settings")
            await state.clear()
            await message.answer("✅ Настройка сохранена.", reply_markup=back_keyboard(back_target))
            return
        if action == "pricing_add":
            row = await service.pricing_add(session, actor, raw)
            await session.commit()
            await state.clear()
            await message.answer(f"✅ Модель {html.escape(row.model)} добавлена.", reply_markup=back_keyboard("adm:ai:pricing"))
            return
        if action == "pricing_edit":
            row = await service.pricing_update(session, actor, int(state_data["pricing_id"]), raw)
            await session.commit()
            await state.clear()
            await message.answer(f"✅ Цены {html.escape(row.model)} обновлены.", reply_markup=back_keyboard(f"adm:pricing:{row.id}"))
            return
        if action == "provider_edit":
            provider_id = int(state_data["provider_id"])
            field = str(state_data["field"])
            row = await service.provider_edit(session, actor, provider_id, field, raw)
            await session.commit()
            await state.clear()
            if field == "display_name":
                text = f"✅ Название для пользователей изменено на <b>{html.escape(row.display_name)}</b>."
            else:
                text = "✅ Провайдер обновлён."
            await message.answer(text, reply_markup=back_keyboard(f"adm:provider:{provider_id}"))
            return
        if action == "promo_validity":
            promo_id = int(state_data["promo_id"])
            row = await service.promo_set_validity_days(session, actor, promo_id, int(raw))
            await session.commit()
            await state.clear()
            await message.answer(
                "✅ Срок действия обновлён.\n\n" + await _promo_detail_text(session, row),
                reply_markup=promo_keyboard(row.id, row.is_active),
            )
            return
        if action == "promo_edit":
            promo_id = int(state_data["promo_id"])
            row = await service.promo_edit_field(session, actor, promo_id, str(state_data["field"]), raw)
            await session.commit()
            await state.clear()
            await message.answer("✅ Промокод обновлён.\n\n" + await _promo_detail_text(session, row), reply_markup=promo_keyboard(row.id, row.is_active))
            return
        if action == "promo_add":
            row = await service.promo_create(session, actor, raw)
            await session.commit()
            await state.clear()
            await message.answer("✅ Промокод создан.\n\n" + await _promo_detail_text(session, row), reply_markup=promo_keyboard(row.id, row.is_active))
            return
        raise TelegramAdminError("Состояние устарело. Откройте /admin заново.")
    except (TelegramAdminError, ValueError) as exc:
        await session.rollback()
        await message.answer(f"❌ {html.escape(str(exc))}\n\nПопробуйте ещё раз или выполните /admin для выхода.")
    except Exception as exc:
        await session.rollback()
        await state.clear()
        logger.exception("Unexpected Telegram admin input error")
        await message.answer(
            "❌ Произошла внутренняя ошибка. Текущее действие отменено.\n\n"
            "Можно продолжить пользоваться ботом или снова открыть /admin."
        )
        raise


@router.message(StateFilter(BroadcastDraft.content))
async def broadcast_content_message(
    message: Message,
    state: FSMContext,
    settings: Settings,
) -> None:
    if not _admin(settings, message.from_user.id if message.from_user else None):
        await state.clear()
        await _deny_message(message)
        return
    telegram_file_id = message.photo[-1].file_id if message.photo else None
    text = (message.caption if message.photo else message.text) or ""
    if not text.strip():
        await message.answer("Нужен текст или фото с подписью.")
        return
    limit = 1024 if telegram_file_id else 4096
    if len(text) > limit:
        await message.answer(f"Слишком длинно. Максимум {limit} символов.")
        return
    await state.update_data(broadcast_text=text, telegram_file_id=telegram_file_id)
    await state.set_state(BroadcastDraft.audience)
    await message.answer("Теперь выберите аудиторию:", reply_markup=audience_keyboard())
