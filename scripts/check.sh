#!/bin/sh
set -eu
python -m compileall -q app alembic tests
alembic heads
alembic upgrade head --sql >/tmp/telegram-ai-saas-alembic.sql
pytest
