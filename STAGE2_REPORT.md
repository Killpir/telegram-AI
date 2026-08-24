# Stage 2 report

## Scope

Implemented the Telegram application layer required for Stage 2:

- aiogram 3 bot process;
- user registration and profile refresh;
- `/start`;
- main menu;
- `/profile`;
- `/help`;
- admin notification for a new user;
- Docker bot service;
- migration and tests.

## Architecture

Telegram handlers do not contain persistence logic. They map Telegram objects to a small `TelegramIdentity` DTO and invoke `UserService`, which delegates database access to `UserRepository`.

A dispatcher-level database middleware creates one async SQLAlchemy session per Telegram update, committing successful updates and rolling back failed updates.

## Registration correctness

Creation uses PostgreSQL `INSERT .. ON CONFLICT DO NOTHING RETURNING id` on the unique `users.telegram_id` key. This keeps registration idempotent even if duplicate `/start` updates are processed close together.

Existing users keep their original registration source/start parameter while mutable Telegram profile data and last activity are refreshed.

## Admin notification

A notification is sent only when the insert actually created a new user. Recipients come from `ADMIN_TELEGRAM_IDS`. Telegram API errors are logged and do not fail the user's `/start` flow.

## Deliberately deferred

The following belong to later stages and are not faked here:

- OpenAI replies/dialog history — Stage 3;
- plans/trial/subscriptions — Stage 4;
- payment providers — Stage 5;
- actual referral rewards — Stage 6;
- web admin toggles/templates — Stage 7+.
