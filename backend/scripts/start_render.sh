#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
alembic upgrade head
python -m app.jobs.worker &
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"
