#!/bin/sh
set -eu

PORT="${PORT:-8080}"
export PORT
ROLE="${OPENAIR_SERVICE_ROLE:-api}"

if [ "$ROLE" = "durable-worker" ]; then
    exec python -m worker.entrypoint
fi

if [ "$ROLE" != "api" ]; then
    echo "OPENAIR_SERVICE_ROLE must be api or durable-worker" >&2
    exit 2
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
