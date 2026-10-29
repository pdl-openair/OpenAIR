#!/usr/bin/env bash
# Linux/macOS mirror of run-baseline.ps1.
# Behavior is intentionally 1:1 with the PowerShell version so that
# REPORT.txt can be compared across platforms.
#
# Platform-specific deviations (intentional):
#   - RUN_DIR default: $XDG_RUNTIME_DIR/openair-product-harness-runs or
#     $HOME/.cache/openair-product-harness-runs (Linux idiom) instead of
#     ~/Downloads (Windows idiom).
#   - Process cleanup uses trap + process-group kill (trap-and-pkill)
#     instead of Get-NetTCPConnection-based PID lookup.
#
# Everything else (port ranges, env var names, isolated dir layout, log
# file names, output banners, REPORT.txt content) matches run-baseline.ps1.

set -euo pipefail

# --- args -------------------------------------------------------------------
SKIP_INSTALL=0
TEST_SPEC=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-install)
      SKIP_INSTALL=1
      shift
      ;;
    --test-spec)
      TEST_SPEC="$2"
      shift 2
      ;;
    --test-spec=*)
      TEST_SPEC="${1#*=}"
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: run-baseline.sh [--skip-install] [--test-spec <path>]
Mirrors run-baseline.ps1 on Linux/macOS.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

# --- paths ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$HARNESS_ROOT/../.." && pwd)"

# JSON isolated mode is fixture/replay-only. Keep durable Research Start and
# worker-backed product specs on the existing production stack so declared and
# exercised persistence modes cannot diverge.
case "${TEST_SPEC,,}" in
  *11-research-start-*|*13-research-start-*|*20-home-question-refinement-live*|*23-representative-research-inputs*|*25-live-agent-audit*|*26-huiyan-*|*27-huiyan-*|*28-huiyan-*|*29-huiyan-*|*30-start-without-implicit-execution*|*31-explicit-single-skill-live*|*32-huiyan-*|*33-real-literature-reliability-flow*|*34-automatic-research-completion*|*35-t10-durable-multiagent-sse*|*36-c3-source-authenticity-mainline*|*37-ot-p0f-golden-path*)
    echo "HARNESS_ENVIRONMENT_CONTRACT_INVALID: '${TEST_SPEC}' requires production-stack mode (PostgreSQL/durable runtime); isolated JSON replay mode is not supported." >&2
    exit 1
    ;;
esac

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "$XDG_RUNTIME_DIR" ]]; then
  RUN_ROOT="${XDG_RUNTIME_DIR}/openair-product-harness-runs"
elif [[ "$(uname)" == "Darwin" ]]; then
  RUN_ROOT="${HOME}/Library/Caches/openair-product-harness-runs"
else
  RUN_ROOT="${HOME}/.cache/openair-product-harness-runs"
fi
RUN_DIR="${RUN_ROOT}/${TIMESTAMP}"
SERVER_LOG_DIR="${RUN_DIR}/server-logs"
ISOLATED_REPO_ROOT="${RUN_DIR}/api"
ISOLATED_API_ROOT="${ISOLATED_REPO_ROOT}/services/api"

# --- state ------------------------------------------------------------------
API_PROCESS=""
WEB_PROCESS=""
EXIT_CODE=1

# --- helpers ----------------------------------------------------------------
log() { echo "$@"; }

resolve_app() {
  # Print the first matching binary on PATH, or empty.
  local names=("$@")
  for name in "${names[@]}"; do
    if command -v "$name" >/dev/null 2>&1; then
      command -v "$name"
      return 0
    fi
  done
  return 1
}

assert_app() {
  local label="$1"; shift
  local path
  path="$(resolve_app "$@")" || {
    echo "Required command not found: $label" >&2
    exit 1
  }
  echo "$path"
}

test_http_ok() {
  local url="$1"
  # --connect-timeout 3, follow redirects, treat any 2xx/3xx as ok.
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' \
    --connect-timeout 3 --max-time 5 "$url" 2>/dev/null || true)"
  [[ "$code" =~ ^[23]..$ ]]
}

wait_http_ok_or_process_exit() {
  local url="$1" timeout="$2" label="$3" pid="$4" stdout_path="$5" stderr_path="$6"
  local deadline=$(( $(date +%s) + timeout ))
  while [[ $(date +%s) -lt $deadline ]]; do
    if test_http_ok "$url"; then
      echo "PASS: $label is ready at $url"
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      log "--- $label STDOUT ---"
      [[ -f "$stdout_path" ]] && tail -n 120 "$stdout_path" 2>/dev/null || echo "LOG_NOT_FOUND=$stdout_path"
      log "--- $label STDERR ---"
      [[ -f "$stderr_path" ]] && tail -n 120 "$stderr_path" 2>/dev/null || echo "LOG_NOT_FOUND=$stderr_path"
      echo "$label process exited before readiness." >&2
      return 1
    fi
    sleep 0.6
  done
  log "--- $label STDOUT ---"
  [[ -f "$stdout_path" ]] && tail -n 120 "$stdout_path" 2>/dev/null || echo "LOG_NOT_FOUND=$stdout_path"
  log "--- $label STDERR ---"
  [[ -f "$stderr_path" ]] && tail -n 120 "$stderr_path" 2>/dev/null || echo "LOG_NOT_FOUND=$stderr_path"
  echo "$label did not become ready within $timeout seconds: $url" >&2
  return 1
}

loopback_port_available() {
  local port="$1"
  # Try to bind to 127.0.0.1:port briefly. nc may not be present; python3 fallback.
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
    s.close()
    sys.exit(0)
except OSError:
    sys.exit(1)
PY
    return $?
  fi
  return 1
}

select_free_loopback_port() {
  local -n candidates=$1
  local label="$2"
  for port in "${candidates[@]}"; do
    if loopback_port_available "$port"; then
      echo "$port"
      return 0
    fi
  done
  echo "No free loopback port found for $label. Candidates=${candidates[*]}" >&2
  exit 1
}

copy_dir_tree() {
  local src="$1" dst="$2"
  if [[ ! -d "$src" ]]; then
    echo "Required source directory not found: $src" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$dst")"
  cp -R "$src" "$dst"
}

# Resolve the API launcher. Priority order mirrors run-baseline.ps1:
#   1. services/api/.venv/bin/python
#   2. uv run python
#   3. python3 / python
# Echoes "<binary> <prefix-args...>" as a single line suitable for eval-style use.
resolve_api_launcher() {
  local venv_python="${REPO_ROOT}/services/api/.venv/bin/python"
  if [[ -x "$venv_python" ]]; then
    echo "$venv_python|"
    return 0
  fi
  if command -v uv >/dev/null 2>&1; then
    echo "$(command -v uv)|run python"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "$(command -v python3)|"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo "$(command -v python)|"
    return 0
  fi
  echo "Required API launcher not found. Expected services/api/.venv, uv, python3, or python." >&2
  exit 1
}

stop_pid_safe() {
  local pid="${1:-}"
  [[ -z "$pid" ]] && return 0
  if kill -0 "$pid" 2>/dev/null; then
    # Kill the whole process group (negative PID) so child uvicorn/vite
    # processes are also cleaned up. Fall back to single PID if pgid miss.
    kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    sleep 0.3
    kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  # Kill in reverse order of startup.
  stop_pid_safe "$WEB_PROCESS"
  stop_pid_safe "$API_PROCESS"
  sleep 0.8

  # Final sweep: any listener still on our chosen ports. Best-effort.
  for port in "$WEB_PORT" "$API_PORT"; do
    if command -v lsof >/dev/null 2>&1; then
      local pids
      pids="$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)"
      [[ -n "$pids" ]] && kill -KILL $pids 2>/dev/null || true
    elif command -v ss >/dev/null 2>&1; then
      local pids
      pids="$(ss -lptn "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)"
      [[ -n "$pids" ]] && kill -KILL $pids 2>/dev/null || true
    fi
  done

  # Always emit the final report so consumers can scrape it.
  local report="${RUN_DIR}/REPORT.txt"
  {
    echo "=== OPENAIR PRODUCT HARNESS P2.1 ISOLATED BASELINE REPORT ==="
    echo "RUN_DIR=$RUN_DIR"
    echo "ISOLATED_RUNTIME_DIR=$ISOLATED_REPO_ROOT"
    echo "API_BASE_URL=$API_BASE_URL"
    echo "WEB_BASE_URL=$WEB_BASE_URL"
    echo "PLAYWRIGHT_EXIT_CODE=$EXIT_CODE"
    echo "PRODUCT_SOURCE_MODIFIED_BY_RUNNER=NO_TRACKED_SOURCE_WRITE_ACTIONS"
    echo "SOURCE_TASK_DATA_MUTATED_BY_RUNNER=NO"
    echo "EXISTING_SERVERS_REUSED=NO"
    echo "IMPORTANT: A non-zero Playwright exit code may be an expected product baseline finding. Review html-report, screenshots, gates, network-console and server logs."
    echo "IMPORTANT: Send the complete terminal output and RUN_DIR back for review."
  } | tee "$report"
}
trap cleanup EXIT

# --- port selection ---------------------------------------------------------
API_PORT_CANDIDATES=(18000 18001 18002 18003 18004 18005 18006 18007 18008 18009)
WEB_PORT_CANDIDATES=(15173 15174 15175 15176 15177 15178 15179 15180 15181 15182)
API_PORT="$(select_free_loopback_port API_PORT_CANDIDATES "isolated OpenAIR API")"
WEB_PORT="$(select_free_loopback_port WEB_PORT_CANDIDATES "OpenAIR web")"
API_BASE_URL="http://127.0.0.1:${API_PORT}"
API_HEALTH_URL="${API_BASE_URL}/health"
WEB_BASE_URL="http://127.0.0.1:${WEB_PORT}"
WEB_BOARD_URL="${WEB_BASE_URL}/workbench"

mkdir -p "$SERVER_LOG_DIR"

export OPENAIR_PRODUCT_HARNESS_RUN_ID="$TIMESTAMP"
export OPENAIR_PRODUCT_HARNESS_RUN_DIR="$RUN_DIR"
export OPENAIR_PRODUCT_HARNESS_API_BASE_URL="$API_BASE_URL"
export OPENAIR_PRODUCT_HARNESS_WEB_BASE_URL="$WEB_BASE_URL"

log "=== OPENAIR PRODUCT HARNESS P2.1 ISOLATED BASELINE ==="
log "REPO_ROOT=$REPO_ROOT"
log "HARNESS_ROOT=$HARNESS_ROOT"
log "RUN_DIR=$RUN_DIR"
log "ISOLATED_RUNTIME_DIR=$ISOLATED_REPO_ROOT"
log "API_BASE_URL=$API_BASE_URL"
log "WEB_BASE_URL=$WEB_BASE_URL"
log "IMPORTANT: existing services on ports 8000 and 5173 are not reused, stopped, or mutated."
log "IMPORTANT: repository data/tasks is never moved or modified by this runner."

# --- main -------------------------------------------------------------------
NODE_PATH="$(assert_app "node" node)"
NPM_PATH="$(assert_app "npm" npm)"
NPX_PATH="$(assert_app "npx" npx)"
PNPM_PATH="$(assert_app "pnpm" pnpm)"
API_LAUNCHER_LINE="$(resolve_api_launcher)"
API_LAUNCHER_BIN="${API_LAUNCHER_LINE%%|*}"
API_LAUNCHER_PREFIX="${API_LAUNCHER_LINE#*|}"
API_LAUNCHER_LABEL="$API_LAUNCHER_BIN"
[[ -n "$API_LAUNCHER_PREFIX" ]] && API_LAUNCHER_LABEL="$API_LAUNCHER_BIN $API_LAUNCHER_PREFIX"

log "NODE=$NODE_PATH"
log "API_LAUNCHER=$API_LAUNCHER_LABEL"

if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  log "[1/8] Ensure OpenAIR web dependencies"
  if [[ ! -d "${REPO_ROOT}/node_modules/.pnpm" ]]; then
    ( cd "$REPO_ROOT" && "$PNPM_PATH" install --frozen-lockfile )
  else
    log "PASS: existing OpenAIR web dependencies reused"
  fi

  log "[2/8] Install harness dependencies and Chromium"
  ( cd "$HARNESS_ROOT" && "$NPM_PATH" ci && "$NPX_PATH" playwright install chromium )
else
  log "[1/8] Reuse installed OpenAIR web dependencies"
  log "[2/8] Reuse installed harness dependencies and Chromium"
fi

log "[3/8] Copy minimal isolated API runtime"
mkdir -p "$ISOLATED_API_ROOT"
copy_dir_tree "${REPO_ROOT}/services/api/app" "${ISOLATED_API_ROOT}/app"
copy_dir_tree "${REPO_ROOT}/fixtures" "${ISOLATED_REPO_ROOT}/fixtures"
copy_dir_tree "${REPO_ROOT}/schemas" "${ISOLATED_REPO_ROOT}/schemas"
copy_dir_tree "${REPO_ROOT}/skills" "${ISOLATED_REPO_ROOT}/skills"
copy_dir_tree "${REPO_ROOT}/mocks" "${ISOLATED_REPO_ROOT}/mocks"
mkdir -p "${ISOLATED_REPO_ROOT}/data/tasks"

log "[4/8] Start isolated OpenAIR API in fixture-seeded replay mode"
log "OPENAIR_PERSISTENCE_BACKEND=json"
API_STDOUT_PATH="${SERVER_LOG_DIR}/api.stdout.log"
API_STDERR_PATH="${SERVER_LOG_DIR}/api.stderr.log"

# Launch in its own process group so cleanup can kill the whole tree.
# shellcheck disable=SC2086
(
  set -e
  cd "$ISOLATED_API_ROOT"
  export OPENAIR_DEV_SEED_FIXTURES=1
  export SKILL_EXEC_MODE=replay
  export OPENAIR_PERSISTENCE_BACKEND=json
  export OPENAIR_AUTH_REQUIRED=1
  export OPENAIR_API_PORT="$API_PORT"
  echo "HARNESS_API_WRAPPER_STARTED"
  exec $API_LAUNCHER_BIN $API_LAUNCHER_PREFIX -u -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$API_PORT" --log-level debug
) >"$API_STDOUT_PATH" 2>"$API_STDERR_PATH" &
API_PROCESS=$!

wait_http_ok_or_process_exit \
  "$API_HEALTH_URL" 45 "isolated OpenAIR API" \
  "$API_PROCESS" "$API_STDOUT_PATH" "$API_STDERR_PATH"

log "[5/8] Start OpenAIR web on isolated port"
WEB_STDOUT_PATH="${SERVER_LOG_DIR}/web.stdout.log"
WEB_STDERR_PATH="${SERVER_LOG_DIR}/web.stderr.log"
(
  set -e
  cd "$REPO_ROOT"
  export VITE_OPENAIR_API_BASE_URL="$API_BASE_URL"
  exec "$PNPM_PATH" --filter '@openair/web' dev \
    --host 127.0.0.1 --port "$WEB_PORT" --strictPort
) >"$WEB_STDOUT_PATH" 2>"$WEB_STDERR_PATH" &
WEB_PROCESS=$!

wait_http_ok_or_process_exit \
  "$WEB_BOARD_URL" 45 "OpenAIR web" \
  "$WEB_PROCESS" "$WEB_STDOUT_PATH" "$WEB_STDERR_PATH"

if [[ -n "$TEST_SPEC" ]]; then
  log "[6/8] Run browser targeted spec: $TEST_SPEC"
else
  log "[6/8] Run browser baseline"
fi
TEST_STDOUT="${RUN_DIR}/playwright.stdout.log"
TEST_STDERR="${RUN_DIR}/playwright.stderr.log"

PLAYWRIGHT_ARGS=(playwright test --project=chromium-desktop)
[[ -n "$TEST_SPEC" ]] && PLAYWRIGHT_ARGS+=("$TEST_SPEC")

set +e
( cd "$HARNESS_ROOT" && "$NPX_PATH" "${PLAYWRIGHT_ARGS[@]}" ) >"$TEST_STDOUT" 2>"$TEST_STDERR"
EXIT_CODE=$?
set -e
[[ -f "$TEST_STDOUT" ]] && cat "$TEST_STDOUT"
[[ -f "$TEST_STDERR" ]] && cat "$TEST_STDERR"

log "[7/8] Baseline evidence generated"
log "[8/8] Preserve short-path isolated runtime under RUN_DIR/api for review"

# cleanup trap will emit REPORT.txt and stop processes.
exit "$EXIT_CODE"
