#!/usr/bin/env bash
# evolve_driver.py 的统一入口：优先用项目 venv 在宿主机直跑（无需 docker）；
# venv 不存在或缺 pymatgen 时，回退到 mattergen:mcp-git 容器内运行。
#
# 优先级：
#   1. 环境变量 EVOLVE_PY 指定的解释器
#   2. 仓库根的 .venv/bin/python（若能 import pymatgen）
#   3. 回退 docker run --rm --network host（需要挂载 run-dir）
set -euo pipefail

D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVER="$D/evolve_driver.py"

# 向上找仓库根（含 .env / .venv 的目录）
root="$D"
for _ in 1 2 3 4 5 6 7; do
  [ -f "$root/.env" ] || [ -d "$root/.venv" ] && break
  p="$(dirname "$root")"
  [ "$p" = "$root" ] && break
  root="$p"
done
VENV_PY="$root/.venv/bin/python"

# 加载 .env：skill 自身目录优先，再向上找项目根；已在环境中的变量不被覆盖。
# MATERIALS_MCP_BASE_URL 等 MCP 端点配置从此读取，不入库。
SKILL_ENV="$D/../.env"
if [ -f "$SKILL_ENV" ]; then
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue ;; esac
    export "$k=${!k:-$v}"
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$SKILL_ENV")
fi
if [ -f "$root/.env" ]; then
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue ;; esac
    export "$k=${!k:-$v}"
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$root/.env")
fi

pick_python() {
  if [ -n "${EVOLVE_PY:-}" ] && "${EVOLVE_PY}" -c 'import pymatgen' 2>/dev/null; then
    echo "$EVOLVE_PY"; return 0
  fi
  if [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import pymatgen' 2>/dev/null; then
    echo "$VENV_PY"; return 0
  fi
  return 1
}

if PY="$(pick_python)"; then
  exec "$PY" "$DRIVER" "$@"
fi

# ---- 回退：容器内运行（pymatgen 只在镜像里）----
echo ">(run.sh) 未找到带 pymatgen 的 venv，回退到容器内运行。" >&2
echo ">(run.sh) 注意：容器内只挂载了 scripts 与当前工作目录；--run-dir 必须在当前目录树下。" >&2
if [ -z "${EVOLVE_IMAGE:-}" ]; then
  echo ">(run.sh) 错误：未设置 EVOLVE_IMAGE 环境变量（无内置默认镜像）。" >&2
  echo ">(run.sh) 请设置 EVOLVE_IMAGE=<registry>/mattergen:mcp-git 或类似镜像，" >&2
  echo ">(run.sh) 或在仓库根 .venv 里安装 pymatgen 后再用宿主机直跑。" >&2
  exit 1
fi
IMAGE="$EVOLVE_IMAGE"
CWD="$(pwd)"
# 把 MCP 端点相关 env 透传进容器（容器内 driver 同样需要）。
PASS_THROUGH=()
for v in MATERIALS_MCP_BASE_URL \
         MCP_MUTATE_URL MCP_RELAX_URL MCP_ALIGNN_URL MCP_EVALUATE_URL MCP_GENERATE_URL; do
  [ -n "${!v:-}" ] && PASS_THROUGH+=( -e "$v=${!v}" )
done
docker run --rm --network host \
  -v "$D":/ttcode/scripts:ro \
  -v "$CWD":"$CWD" \
  -w /ttcode/scripts \
  "${PASS_THROUGH[@]}" \
  "$IMAGE" \
  /opt/conda/envs/mattergen/bin/python evolve_driver.py "$@"
