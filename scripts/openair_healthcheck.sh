#!/bin/sh
# Role-aware container healthcheck for Dockerfile.api.
# api           → HTTP GET /health
# durable-worker → worker.entrypoint process must be running (no HTTP server)
set -eu

ROLE="${OPENAIR_SERVICE_ROLE:-api}"
PORT="${PORT:-8080}"

if [ "$ROLE" = "durable-worker" ]; then
  for cmdline in /proc/[0-9]*/cmdline; do
    [ -r "$cmdline" ] || continue
    if tr '\000' ' ' <"$cmdline" 2>/dev/null | grep -q 'worker.entrypoint'; then
      exit 0
    fi
  done
  exit 1
fi

curl -fs "http://127.0.0.1:${PORT}/health" >/dev/null
