#!/usr/bin/env bash
# formula_driver.py 的统一入口：优先用项目 venv 在宿主机直跑（无需 docker）；
# venv 不存在或缺 pymatgen 时，回退到 mattergen:mcp-git 容器内运行。
set -euo pipefail

D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVER="$D/formula_driver.py"

# 向上找仓库根（含 .env / .venv 的目录）
root="$D"
for _ in 1 2 3 4 5 6 7; do
  [ -f "$root/.env" ] || [ -d "$root/.venv" ] && break
  p="$(dirname "$root")"
  [ "$p" = "$root" ] && break
  root="$p"
done
VENV_PY="$root/.venv/bin/python"

# 加载 .env（若存在）
if [ -f "$root/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$root/.env"
  set +a
fi

pick_python() {
  if [ -n "${FORMULA_PY:-}" ] && "${FORMULA_PY}" -c 'import pymatgen' 2>/dev/null; then
    echo "$FORMULA_PY"; return 0
  fi
  if [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import pymatgen' 2>/dev/null; then
    echo "$VENV_PY"; return 0
  fi
  return 1
}

if PY="$(pick_python)"; then
  exec "$PY" "$DRIVER" "$@"
fi

echo ">(run.sh) 未找到带 pymatgen 的 venv，回退到容器内运行。" >&2
echo ">(run.sh) 注意：容器内只挂载了 scripts 与当前工作目录；--run-dir 必须在当前目录树下。" >&2
IMAGE="${FORMULA_IMAGE:-192.0.2.11:8888/pipeline-service/mattergen:mcp-git}"
CWD="$(pwd)"
PASS_THROUGH=()
for v in MATERIALS_MCP_BASE_URL \
         MCP_DIFFCSP_URL MCP_RELAX_URL MCP_ALIGNN_URL MCP_EVALUATE_URL MCP_GENERATE_URL; do
  [ -n "${!v:-}" ] && PASS_THROUGH+=( -e "$v=${!v}" )
done
docker run --rm --network host \
  -v "$D":/ttcode/scripts:ro \
  -v "$CWD":"$CWD" \
  -w /ttcode/scripts \
  "${PASS_THROUGH[@]}" \
  "$IMAGE" \
  /opt/conda/envs/mattergen/bin/python formula_driver.py "$@"
