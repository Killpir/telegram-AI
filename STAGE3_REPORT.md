# Stage 3 report — OpenAI chat, dialogs, context, usage and limits

## Goal

Implement specification Stage 3 on top of Stage 2 without breaking existing user registration/menu behavior:

- OpenAI Responses API;
- dialogs;
- persisted message history;
- limited context;
- automatic summarization;
- token/cost accounting;
- abuse/economy limits;
- long Telegram response handling.

## Files added

```text
app/ai/client.py
app/ai/config.py
app/ai/context.py
app/ai/limits.py
app/ai/pricing.py
app/ai/service.py
app/ai/summarizer.py
app/ai/usage.py

app/db/models/ai.py

app/dialogs/repository.py
app/dialogs/service.py

app/bot/handlers/chat.py
app/bot/handlers/new_dialog.py
app/bot/utils.py

alembic/versions/20260818_0003_ai_chat.py

tests/test_ai_client.py
tests/test_ai_context.py
tests/test_ai_limits.py
tests/test_ai_model_metadata.py
tests/test_ai_pricing.py
tests/test_bot_utils.py
```

## Important files changed

```text
app/config.py
app/db/__init__.py
app/db/models/__init__.py
app/bot/__init__.py
app/bot/factory.py
app/bot/main.py
app/bot/handlers/__init__.py
app/bot/handlers/fallback.py
app/bot/handlers/help.py
app/bot/handlers/start.py
app/bot/keyboards/main.py

docker-compose.yml
.env.example
pyproject.toml
README.md
```

## Database migration

New revision:

```text
20260818_0003
```

Tables:

```text
dialogs
messages
ai_model_pricing
ai_usage
```

`dialogs` keeps independent conversations and a compact cumulative summary.

`messages` keeps complete user/assistant text. User messages are initially `pending`, become `completed` after a successful AI call, or `failed` after an OpenAI failure. Failed messages remain auditable but are excluded from future context.

`ai_usage` records both normal chat calls and summarization calls with:

```text
user_id
dialog_id
request_kind
model
input_tokens
cached_input_tokens
output_tokens
reasoning_tokens
cost_usd
duration_ms
status
error
openai_response_id
request_id
created_at
```

`ai_model_pricing` stores model prices in the database rather than Telegram handlers.

Initial seed:

```text
gpt-5-mini
input:        0.25 USD / 1M
cached input: 0.025 USD / 1M
output:       2.00 USD / 1M
```

## Context strategy

The project deliberately does not send the user's entire database history with every request.

Normal context:

```text
OpenAI instructions
+
dialog.summary
+
last configured N completed non-summarized messages
+
current user message
```

The context character budget is configurable.

When the unsummarized history reaches a threshold, old messages are summarized while the newest N messages stay verbatim. The summary request has its own `AIUsage` row and cost.

## OpenAI client behavior

The client calls:

```text
POST /v1/responses
```

and uses:

```text
model
instructions
input[]
max_output_tokens
reasoning.effort (when configured)
temperature (only when configured)
store=false
```

Text output is extracted by walking all `output` message/content items instead of assuming `output[0]` is text.

Usage extraction supports:

```text
usage.input_tokens
usage.input_tokens_details.cached_tokens
usage.output_tokens
usage.output_tokens_details.reasoning_tokens
```

Safe retries are deliberately narrow:

- connection establishment error: retry once;
- explicit HTTP 429: retry once;
- read/write timeout: no automatic retry because the upstream request may already have been processed.

## Cost protection

Before sending the primary OpenAI request, the service requires an active pricing row for the selected model. Missing pricing fails closed instead of generating an unpriced API expense.

Cost is persisted at request time, so future admin price changes do not mutate historical usage economics.

## Limits

Stage 3 implements infrastructure limits before Stage 4 subscription limits:

```text
requests/minute       Redis
requests/day          PostgreSQL AIUsage
requests/month        PostgreSQL AIUsage
monthly input tokens  PostgreSQL AIUsage
monthly output tokens PostgreSQL AIUsage
max input characters  runtime setting
max output tokens     Responses API parameter
```

The user sees simple limit messages; internal token accounting details are not exposed.

## Concurrency

A per-user Redis lease prevents two OpenAI responses from being generated against the same dialog simultaneously.

The same lease is used when creating a new dialog, so this sequence is prevented:

```text
AI request starts
/new rotates active dialog
old AI request finishes into the wrong dialog
```

Database row locking plus a PostgreSQL partial unique index provide a second line of protection for dialog rotation.

## Telegram behavior

- `/new` creates a new independent active dialog.
- `💬 Новый диалог` does the same.
- normal text goes to AI.
- typing action is displayed while the model is working.
- long output is split into chunks below Telegram's limit.
- AI output disables HTML parse mode, so arbitrary model text cannot break Telegram HTML parsing.
- dialogue/usage data is committed before sending the external Telegram response.

## Verification performed

Available local checks:

```text
python -m compileall -q app alembic tests
pytest -ra
alembic heads
alembic upgrade head --sql
YAML parse of docker-compose.yml
```

Result in the current environment:

```text
20 passed
3 skipped
```

Skipped tests are only those whose runtime packages are unavailable in this execution environment:

```text
redis
aiogram
celery
```

The project declares all three as normal dependencies in `pyproject.toml`; a normal `pip install -e '.[test]'` or Docker image build installs them.

Alembic offline generation succeeded through:

```text
20260818_0003 (head)
```

Docker Compose YAML parsed successfully with services:

```text
api
bot
migrate
nginx
postgres
redis
worker
```

Docker Engine itself is not installed in this execution environment, so an actual `docker compose up` cannot be run here.

## Manual verification after download

```bash
cp .env.example .env
# set BOT_TOKEN, OPENAI_API_KEY, PostgreSQL password/URL and secrets

docker compose up -d --build
docker compose ps
docker compose logs -f bot
```

Telegram smoke test:

```text
/start
hello
follow-up question
/new
question that must not use the previous dialog context
/profile
```

Database checks can then verify `dialogs`, `messages`, and `ai_usage` are being populated.

## Next

Stage 4:

```text
plans
trial
subscriptions
subscription/trial usage gates
renewal semantics
```

The Stage 3 internal limits remain underneath those customer-facing subscription limits as economy protection.
