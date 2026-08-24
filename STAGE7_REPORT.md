# Stage 7 Report — Web Admin Panel

Дата: 2026-08-19

## Что реализовано

Этап 7 добавляет полноценную серверную веб-админку FastAPI + Jinja2 без тяжёлого SPA.

### Авторизация и безопасность

- `/admin/login` и `/admin/logout`.
- Роли `superadmin` и `admin`.
- Argon2-хэширование паролей.
- Интерактивное создание первого superadmin через `python -m app.admin.cli create-superadmin`.
- Signed HttpOnly session cookie, `SameSite=Lax`, `Secure` в staging/production.
- Ротация сессии после входа.
- CSRF на всех POST-формах, включая login/logout.
- Superadmin-only создание дополнительных администраторов.
- AuditLog для входов, неудачных входов и административных изменений.
- Security headers: frame deny, nosniff, referrer/permissions policy, HSTS в production/staging.
- Секреты провайдеров не попадают в БД/HTML админки.

### Разделы

- Dashboard.
- Пользователи.
- Подписки.
- Платежи.
- Тарифы.
- Trial.
- AI-настройки и цены моделей.
- Реферальная система.
- Промокоды.
- Платёжные системы.
- Ошибки.
- AuditLog.
- Настройки сервиса.
- Администраторы (только superadmin).

Рассылки и плановые уведомления намеренно не имитируются: они остаются этапами 9 и 10.

### Dashboard

Уже показывает базовую операционную сводку:

- всего пользователей;
- новых сегодня / 7 / 30 дней;
- активных сегодня / 7 дней;
- заблокировавших бота;
- активные подписки и trial;
- подписки, заканчивающиеся в ближайшие 3 дня;
- выручку сегодня / 7 / 30 дней / всё время;
- количество успешных AI вызовов;
- стоимость OpenAI за 30 дней;
- количество нерешённых ErrorEvent.

Полная аналитика, произвольные периоды, conversion/economics и графики относятся к этапу 8.

### Тарифы

- Создание нового тарифа.
- Редактирование всех основных полей.
- ON/OFF.
- recommended.
- RUB / Stars / USD.
- duration / requests / smart requests / token budgets / max output.
- Безопасное удаление только если отсутствуют ссылки из subscriptions/payments/promo codes.
- Опасное удаление требует UI-confirmation.

### AI

Из админки редактируются runtime-параметры AI и таблица `AIModelPricing`.

Добавление/редактирование цены модели включает:

- input / 1M tokens;
- cached input / 1M;
- output / 1M;
- active flag.

Админка не позволяет переключить primary/summary model на модель, для которой отсутствует pricing row.

### Платёжные системы

Для каждого поддержанного провайдера доступны:

- ON/OFF;
- test mode;
- fee percent;
- fixed RUB fee;
- sort order;
- индикатор наличия обязательных credentials.

Сами credentials не отображаются.

### Промокоды

Есть создание и редактирование:

- code;
- target plan;
- dates;
- max activations;
- per-user limit;
- discount percent;
- fixed RUB discount;
- free days;
- ordinary/smart requests;
- ON/OFF.

### Настройки сервиса

Добавлены `AppSetting`:

- `service.name`;
- `service.bot_username`;
- `service.support_username`;
- `service.welcome_text`;
- `service.help_text`;
- `service.maintenance_mode`;
- `service.maintenance_text`.

Welcome/help/support/bot username/maintenance подключены к Telegram-боту, а не являются только формой в панели.

Maintenance mode блокирует обычных пользователей и оставляет доступ Telegram ID из `ADMIN_TELEGRAM_IDS`.

## Миграция

Новая миграция:

```text
20260819_0007_admin_panel.py
```

Изменения:

- `admins.last_login_at`;
- seed runtime service settings.

Alembic head:

```text
20260819_0007
```

## Изменённая структура

```text
app/admin/
├── cli.py
├── repository.py
├── router.py
├── security.py
├── service.py
├── templating.py
├── static/
│   └── admin.css
└── templates/admin/
    ├── base.html
    ├── login.html
    ├── dashboard.html
    ├── users.html
    ├── subscriptions.html
    ├── payments.html
    ├── plans.html
    ├── trial.html
    ├── ai.html
    ├── payment_providers.html
    ├── referrals.html
    ├── promocodes.html
    ├── promo_fields.html
    ├── errors.html
    ├── audit.html
    ├── settings.html
    └── admins.html

app/bot/middlewares/maintenance.py
app/services/runtime_settings.py
alembic/versions/20260819_0007_admin_panel.py
```

## Проверка

В доступной среде выполнено:

```text
python -m compileall app alembic tests  OK
pytest -q -ra                            58 passed, 3 skipped
Alembic head                            20260819_0007
Alembic full offline upgrade            OK
Jinja template compilation              OK
SQLAlchemy metadata                     19 tables
```

Пропущены только runtime-тесты, которым локально требуются отсутствующие пакеты:

```text
redis
aiogram
celery
```

Они остаются объявленными production-зависимостями Docker-сборки.

Docker Engine в текущей среде отсутствует, поэтому фактический `docker compose up` здесь не заявляется как проверенный.

## Первый запуск админки

После запуска Compose и миграций:

```bash
docker compose exec api python -m app.admin.cli create-superadmin
```

После создания учётки:

```text
http(s)://<domain>/admin
```

## Следующий этап

Этап 8:

- расширенный поиск пользователей;
- все фильтры из ТЗ;
- полная карточка пользователя;
- ручное управление подпиской/лимитами/block/unblock;
- user economics;
- статистика по произвольному периоду;
- графики и conversion metrics.
