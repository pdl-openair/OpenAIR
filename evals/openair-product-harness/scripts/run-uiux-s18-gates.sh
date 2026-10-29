#!/usr/bin/env bash
# Linux/macOS mirror of run-uiux-s18-gates.ps1.
# Thin wrapper around run-baseline.sh targeting the S18 content readability spec.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_SPEC="tests/08-uiux-s18-content-readability.spec.ts"

echo "=== OPENAIR UIUX HARNESS S18 CONTENT READABILITY RUNNER ==="
echo "TARGET_SPEC=$TARGET_SPEC"
echo "FULL_BASELINE=NOT_RUN"
echo "PRODUCT_SOURCE_MODIFIED=NO"

BASELINE_SCRIPT="${SCRIPT_DIR}/run-baseline.sh"
if [[ ! -x "$BASELINE_SCRIPT" ]]; then
  echo "Missing or non-executable baseline runner: $BASELINE_SCRIPT" >&2
  exit 1
fi

ARGS=(--test-spec "$TARGET_SPEC")
for arg in "$@"; do
  case "$arg" in
    --skip-install) ARGS+=(--skip-install) ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

"$BASELINE_SCRIPT" "${ARGS[@]}"
EXIT_CODE=$?

echo "PLAYWRIGHT_EXIT_CODE=$EXIT_CODE"
echo "NOTE: Non-zero Playwright exit code may indicate S18 readability gate findings."
exit "$EXIT_CODE"
