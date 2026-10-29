#!/bin/bash
#=============================================================================
# 一键 HPC 任务自动化脚本 (Linux)
#
# 这是一个模板脚本: 通过 jcc CLI 把本地 VASP 输入目录上传到 HPC,
# 执行任务, 下载结果 zip.
#
# 前提: 用户需要自行安装 jcc CLI 工具, 并通过环境变量提供凭证:
#   export JCC_BASE_URL="https://jcc.jointcloud.net"
#   export JCC_USERNAME="your_username"
#   export JCC_PASSWORD="your_password"
#   export JCC_CLUSTER_NAME="HPCSlurmArm"     # 可选, 默认 HPCSlurmArm
#   export JCC_APP_TYPE="vasp"                # 可选, 默认 vasp
#
# 用法:
#   ./run_hpc.sh --local-path <路径> --output-dir <目录>
#=============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 加载 .env：skill 自身目录优先，已设的环境变量不被覆盖 ──
_load_env_file() {
  local f="$1"
  [ -f "$f" ] || return 0
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue ;; esac
    v="${v%\"}"; v="${v#\"}"
    export "$k=${!k:-$v}"
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$f")
}
_load_env_file "$SCRIPT_DIR/../.env"          # skill 自身
# 项目根后备：从脚本位置（或拷贝后的工作目录）向上逐级找 .env（跳过已加载的 skill 自身）
_d="$SCRIPT_DIR"
for _ in 1 2 3 4 5 6; do
  _d="$(dirname "$_d")"
  [ "$_d" = "$SCRIPT_DIR/.." ] && continue
  [ -f "$_d/.env" ] && { _load_env_file "$_d/.env"; break; }
done
unset _d

PYTHON="${MATERIALS_PY:-${EVOLVE_PY:-python}}"
# MATERIALS_PY/EVOLVE_PY 可能来自镜像携带的宿主机 .env（Windows 路径在 Linux
# 容器内不可执行，task_e3ae1db3 教训：zipfile 探测 rc=1 且零 stdout）。
# 探测失败回落 PATH 上的 python，而不是直接退出。
if ! "$PYTHON" -I -c "import zipfile" >/dev/null 2>&1; then
    PYTHON=python
fi

# ============ 参数解析 ============
usage() {
    echo "用法: $0 --local-path <路径> --output-dir <目录> [--task-name <名称>] [--nodes N] [--ntasks N]"
    echo ""
    echo "必选参数:"
    echo "  --local-path   上传到 HPC 的本地文件/目录路径"
    echo "  --output-dir   结果下载目录"
    echo ""
    echo "可选参数:"
    echo "  --task-name    任务名称 (建议包含材料化学式, 如 AlInSe3_10at_relax)"
    echo "                 未指定时回退到随机后缀 (不推荐)"
    echo "  --nodes        计算节点数 (默认: \$JCC_NODES 或 1)"
    echo "  --ntasks       总 MPI 进程数 (默认: \$JCC_NTASKS 或 4)"
    echo "                 注: jcc --help 文档写'每个节点任务数'是错误的, 实测确认就是总进程数"
    echo "                 (如 nodes=2 ntasks=8, VASP 日志显示 'running on 8 total cores')"
    echo "                 配合 INCAR 中的 KPAR/NCORE 实现并行加速 (例如 ntasks=8 + KPAR=2 NCORE=4)"
    echo "                 大体系注意 GLEX OOM: 见下方'GLEX 内存阈值'说明"
    echo ""
    echo "必需环境变量:"
    echo "  JCC_BASE_URL    jcc 服务地址"
    echo "  JCC_USERNAME    jcc 用户名"
    echo "  JCC_PASSWORD    jcc 密码"
    echo ""
    echo "可选环境变量:"
    echo "  JCC_CLUSTER_NAME  集群名 (默认: HPCSlurmArm)"
    echo "  JCC_APP_TYPE      应用类型 (默认: vasp)"
    echo "  JCC_NODES         默认节点数 (默认: 1)"
    echo "  JCC_NTASKS        默认总 MPI 进程数 (默认: 4)"
    echo "  POLL_SECONDS      状态轮询间隔秒 (默认: 15)"
    echo ""
    echo "示例:"
    echo "  $0 --local-path ./AlInSe3_10at/relax --output-dir ./results/AlInSe3_10at_relax \\"
    echo "     --task-name AlInSe3_10at_relax"
    echo "  # 中等体系 (≤30 原子) 用 8 核加速:"
    echo "  $0 --local-path ./Te2I_24at/relax --output-dir ./results/Te2I_24at_relax \\"
    echo "     --task-name Te2I_24at_relax --nodes 2 --ntasks 8"
    echo "  # 大体系 (≥50 原子) GLEX 内存注册可能 OOM, 降到 4 核:"
    echo "  $0 --local-path ./Se_64at/relax --output-dir ./results/Se_64at_relax \\"
    echo "     --task-name Se_64at_relax --nodes 1 --ntasks 4"
    exit 1
}

LOCAL_PATH=""
OUTPUT_DIR=""
TASK_NAME_OVERRIDE=""
NODES_OVERRIDE=""
NTASKS_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local-path)
            LOCAL_PATH="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --task-name)
            TASK_NAME_OVERRIDE="$2"
            shift 2
            ;;
        --nodes)
            NODES_OVERRIDE="$2"
            shift 2
            ;;
        --ntasks)
            NTASKS_OVERRIDE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "未知参数: $1"
            usage
            ;;
    esac
done

if [ -z "$LOCAL_PATH" ]; then
    echo "错误: 必须指定 --local-path"
    usage
fi
if [ -z "$OUTPUT_DIR" ]; then
    echo "错误: 必须指定 --output-dir"
    usage
fi
if [ ! -e "$LOCAL_PATH" ]; then
    echo "错误: 路径不存在: $LOCAL_PATH"
    exit 1
fi

# 下载/解压均不得覆盖之前任务的产物；已有非空目录也拒绝以免复用旧内容。
if [ -e "$OUTPUT_DIR" ] && { [ -e "$OUTPUT_DIR/download" ] || [ -n "$(ls -A "$OUTPUT_DIR" 2>/dev/null)" ]; }; then
    echo "错误: 输出目录已有结果，请换新目录或直接使用已有结果，避免重复提交"
    exit 1
fi
[ -f "$SCRIPT_DIR/hpc_results.py" ] || { echo "缺少同目录 hpc_results.py"; exit 1; }
"$PYTHON" -I -c "import zipfile" || exit 1

# ============ 从环境变量读取配置 (不写死任何凭证) ============
JCC_BASE_URL="${JCC_BASE_URL:-}"
USERNAME="${JCC_USERNAME:-}"
PASSWORD="${JCC_PASSWORD:-}"
CLUSTER_NAME="${JCC_CLUSTER_NAME:-HPCSlurmArm}"
APP_TYPE="${JCC_APP_TYPE:-vasp}"
POLL_SECONDS="${POLL_SECONDS:-15}"
# 资源配置: CLI 覆盖 > 环境变量 > 默认
NODES="${NODES_OVERRIDE:-${JCC_NODES:-1}}"
NTASKS="${NTASKS_OVERRIDE:-${JCC_NTASKS:-4}}"

if [ -z "$JCC_BASE_URL" ] || [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
    echo "错误: 缺少 jcc 凭证环境变量"
    echo "请设置 JCC_BASE_URL, JCC_USERNAME, JCC_PASSWORD"
    echo ""
    echo "jcc CLI 安装与凭证获取请联系 HPC 管理员"
    exit 1
fi

# 检查 jcc 是否已安装
if ! command -v jcc >/dev/null 2>&1; then
    echo "错误: 未找到 jcc CLI 工具"
    echo "请先安装 jcc 并加入 PATH"
    exit 1
fi

# 检查 jq 是否已安装
if ! command -v jq >/dev/null 2>&1; then
    echo "错误: 未找到 jq 工具 (用于解析 jcc 输出)"
    echo "请先安装 jq"
    exit 1
fi


# ============ 颜色 ============
C_CYAN='\033[0;36m'
C_GREEN='\033[0;32m'
C_YELLOW='\033[0;33m'
C_RED='\033[0;31m'
C_DARK_GRAY='\033[0;90m'
C_DARK_YELLOW='\033[0;33m'
C_RESET='\033[0m'


# ============ 生成随机名称 ============
generate_random_suffix() {
    local length=${1:-8}
    local chars='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    local result=''
    for ((i=0; i<length; i++)); do
        result+=${chars:RANDOM%${#chars}:1}
    done
    echo "$result"
}

RANDOM_SUFFIX=$(generate_random_suffix)
# 优先使用用户提供的任务名 (应包含材料化学式); 否则回退到随机后缀
if [ -n "$TASK_NAME_OVERRIDE" ]; then
    TASK_NAME="$TASK_NAME_OVERRIDE"
    PACKAGE_NAME="${TASK_NAME_OVERRIDE}-pkg-${RANDOM_SUFFIX}"
else
    TASK_NAME="vasp-task-${RANDOM_SUFFIX}"
    PACKAGE_NAME="vasp-test-${RANDOM_SUFFIX}"
fi


separator() {
    local color="${1:-$C_CYAN}"
    echo -e "${color}============================================${C_RESET}"
}

echo ""
separator "$C_CYAN"
echo -e "${C_CYAN}  HPC 任务自动化脚本${C_RESET}"
separator "$C_CYAN"
echo -e "  Package : ${PACKAGE_NAME}"
echo -e "  Task    : ${TASK_NAME}"
echo -e "  Cluster : ${CLUSTER_NAME}"
echo -e "  AppType : ${APP_TYPE}"
echo -e "  Nodes   : ${NODES}    Ntasks: ${NTASKS}"
separator "$C_CYAN"
echo ""

# ============ 登录 ============
echo -e "${C_YELLOW}[0/5] 登录...${C_RESET}"
export JCC_BASE_URL
LOGIN_ERR_LOG="${TMPDIR:-/tmp}/jcc_login_err.log"
login_output=$(jcc login --username "$USERNAME" --password "$PASSWORD" 2>"$LOGIN_ERR_LOG") || {
    echo -e "${C_RED}[FAIL] 登录失败，退出码: $?${C_RESET}"
    cat "$LOGIN_ERR_LOG"
    exit 1
}
# jcc login 在 JSON 前会打印一行 "POST https://..." 日志, 需要先抽出 JSON 块再 jq
login_json=$(echo "$login_output" | awk '/^\{/{flag=1} flag{print} /^\}/{if(flag)exit}')
if [ -z "$login_json" ]; then
    echo -e "${C_RED}[FAIL] 登录响应未包含 JSON: ${C_RESET}"
    echo "$login_output"
    exit 1
fi
export JCC_TOKEN=$(echo "$login_json" | jq -r '.token // empty')
if [ -z "$JCC_TOKEN" ] || [ "$JCC_TOKEN" = "null" ]; then
    echo -e "${C_RED}[FAIL] 登录响应中未找到 token${C_RESET}"
    echo "$login_json"
    exit 1
fi
# 顺带导出 userID, 部分 CLI 子命令 (train/inference detail 等) 会用到
USER_ID=$(echo "$login_json" | jq -r '.userID // empty')
[ -n "$USER_ID" ] && [ "$USER_ID" != "null" ] && export JCC_USER_ID="$USER_ID"
echo -e "${C_DARK_GRAY}       Token 已获取${C_RESET}"

# ============ Step 1: 创建并上传文件 ============
echo -e "${C_YELLOW}[1/5] 创建 Package 并上传文件...${C_RESET}"
echo -e "${C_DARK_GRAY}       Path: ${LOCAL_PATH}${C_RESET}"

# 上传并校验包内文件 (规避平台小文件偶发丢失, 见 vasp-band-gap/references/jcc-quirks.md #4)
# 返回: 0=文件齐全  1=文件缺失(已删包,可重试)  2=创建失败(不可重试)
upload_package() {
    local pkg="$1" create_out detail f missing=0
    create_out=$(jcc commonfile create --name "$pkg" --cluster-name "$CLUSTER_NAME" --path "$LOCAL_PATH" 2>&1) || {
        echo -e "${C_RED}[FAIL] 创建 Package 失败${C_RESET}"
        echo "$create_out"
        return 2
    }
    PACKAGE_ID=$(echo "$create_out" | tail -1 | tr -d '[:space:]')
    if [ -z "$PACKAGE_ID" ]; then
        echo -e "${C_RED}[FAIL] 未能提取 packageID${C_RESET}"
        return 2
    fi
    # 校验: 包内文件列表须包含本地目录每个文件名
    detail=$(jcc commonfile query --package-name "$pkg" --output json 2>&1) || {
        echo -e "${C_DARK_YELLOW}[WARN] 无法查询包文件列表, 跳过校验${C_RESET}"
        return 0
    }
    # 详情 JSON 内含真实 packageID (schema: .data.uploadedDatas[0].objects[0].packageID)
    local pid
    pid=$(echo "$detail" | awk '/^\{/{flag=1} flag{print} /^\}/{if(flag)exit}' | jq -r '.data.uploadedDatas[0].objects[0].packageID // .data.packageID // empty' 2>/dev/null || true)
    [ -n "$pid" ] && [ "$pid" != "null" ] && PACKAGE_ID="$pid"
    for f in $(ls -1 "$LOCAL_PATH"); do
        # 详情 JSON 中文件名形如 "path": "/POTCAR" (带前导斜杠)
        if ! echo "$detail" | grep -qE "\"/?$f\""; then
            echo -e "${C_DARK_YELLOW}[WARN] 文件未出现在包内: $f${C_RESET}"
            missing=1
        fi
    done
    if [ "$missing" -eq 0 ]; then
        return 0
    fi
    jcc commonfile delete --package-name "$pkg" --force >/dev/null 2>&1 || true
    return 1
}

PKG_ATTEMPT_MAX="${PKG_ATTEMPT_MAX:-4}"
packageID=""
for ((attempt=1; attempt<=PKG_ATTEMPT_MAX; attempt++)); do
    pkg_try="$PACKAGE_NAME"
    [ "$attempt" -gt 1 ] && pkg_try="${PACKAGE_NAME}-retry${attempt}"
    echo -e "${C_DARK_GRAY}       (attempt ${attempt}) package=${pkg_try}${C_RESET}"
    # 注意: 本脚本 set -e, 不能用 "cmd; rc=$?" 写法 (非零返回会直接终止脚本)
    upload_package "$pkg_try" && rc=0 || rc=$?
    if [ "$rc" -eq 0 ]; then
        PACKAGE_NAME="$pkg_try"
        packageID="$PACKAGE_ID"
        break
    fi
    if [ "$rc" -eq 2 ]; then
        exit 1
    fi
    sleep 3
done
if [ -z "$packageID" ]; then
    echo -e "${C_RED}[FAIL] 上传 ${PKG_ATTEMPT_MAX} 次后文件仍不完整${C_RESET}"
    exit 1
fi
echo -e "${C_GREEN}       packageID = ${packageID} (文件校验通过)${C_RESET}"

# ============ Step 2: 创建超算任务 ============
echo -e "${C_YELLOW}[2/5] 创建 HPC 任务...${C_RESET}"

task_output=$(jcc hpc create --name "$TASK_NAME" --apptype "$APP_TYPE" \
             --cluster-name "$CLUSTER_NAME" --package-name "$PACKAGE_NAME" \
             --nodes "$NODES" --ntasks "$NTASKS" --output json 2>/dev/null); create_rc=$?
if [ "$create_rc" -ne 0 ]; then
    echo -e "${C_RED}[FAIL] 创建任务失败${C_RESET}"
    # 再次执行一次捕获 stderr 供诊断
    jcc hpc create --name "$TASK_NAME" --apptype "$APP_TYPE" \
        --cluster-name "$CLUSTER_NAME" --package-name "$PACKAGE_NAME" \
        --nodes "$NODES" --ntasks "$NTASKS" --output json 2>&1 || true
    exit 1
fi
taskID=$(echo "$task_output" | awk '/^\{/{flag=1} flag{print} /^\}/{if(flag)exit}' | jq -r '.taskId // .data.taskId // .data.jobSetID // .data.subTaskInfos[0].id // empty' 2>/dev/null || true)
if [ -z "$taskID" ]; then
    # taskID 仅用于展示, 后续操作均按任务名进行, 提取失败不阻塞
    echo -e "${C_DARK_YELLOW}[WARN] 未能从响应提取 taskID (不影响任务执行)${C_RESET}"
    taskID="-"
fi
echo -e "${C_GREEN}       taskID = ${taskID}${C_RESET}"

# CLI 的 Go JSON 解析失败不能在 shell 中修复；保存原始错误及 verbose 响应。
mkdir -p "$OUTPUT_DIR"
LOG_BROKEN=0
show_task_log() {
    [ "$LOG_BROKEN" -eq 0 ] || return 0
    local text rc=0
    text=$(jcc hpc log --task-name "$TASK_NAME" 2>&1) || rc=$?
    printf '%s\n' "$text" | tee -a "$OUTPUT_DIR/hpc.log"
    if [ "$rc" -ne 0 ] || [[ "$text" == *"unmarshal"* ]]; then
        LOG_BROKEN=1
        jcc hpc log --task-name "$TASK_NAME" --verbose > "$OUTPUT_DIR/hpc-log-diagnostic.log" 2>&1 || true
        echo "[WARN] 日志 CLI 失败，保存诊断且停止重复刷错；以 status 为准，不推断 VASP 根因。"
    fi
}

# ============ Step 3: 监控任务状态 ============
echo -e "${C_YELLOW}[3/5] 监控任务状态（每 ${POLL_SECONDS}s 刷新）...${C_RESET}"

while true; do
    # 当前 jcc 版本只支持 --task-name (不支持 --task-id); 见 references/README.md
    # 注意必须 --output json, 默认 text 输出无 JSON 块可解析
    status_output=$(jcc hpc status --task-name "$TASK_NAME" --output json 2>&1) || true
    printf '%s\n' "$status_output" > "$OUTPUT_DIR/status.log"

    status=$(echo "$status_output" | awk '/^\{/{flag=1} flag{print} /^\}/{if(flag)exit}' | jq -r '.data.subTaskInfos[0].status // empty' 2>/dev/null || true)
    if [ -z "$status" ]; then
        echo -e "${C_DARK_YELLOW}[WARN] 状态解析失败，原始输出已写入 $OUTPUT_DIR/status.log；10s 后重试...${C_RESET}"
        sleep 10
        continue
    fi

    timestamp=$(date +%H:%M:%S)
    echo -e "${C_YELLOW}[${timestamp}] 状态: ${status}${C_RESET}"

    case "$status" in
        Completed)
            echo -e "${C_GREEN}[OK] 任务执行完成!${C_RESET}"
            break
            ;;
        Failed|Error|Cancelled|Timeout|Rejected|Aborted)
            echo -e "${C_RED}[FAIL] 任务异常终止: ${status}${C_RESET}"
            echo -e "${C_DARK_YELLOW}--- 最终日志 ---${C_RESET}"
            show_task_log
            jcc hpc result --task-name "$TASK_NAME" --output json > "$OUTPUT_DIR/result-list.log" 2>&1 || true
            exit 1
            ;;
    esac

    show_task_log
    sleep "$POLL_SECONDS"
done

# ============ Step 4 & 5: 等待结果就绪并下载 ============
# 结果 zip 在任务 Completed 后需 2-10 分钟传播 (jcc-quirks #2), 预算可环境变量覆盖
RESULT_MAX_RETRIES="${RESULT_MAX_RETRIES:-30}"
RESULT_RETRY_INTERVAL="${RESULT_RETRY_INTERVAL:-20}"

echo -e "${C_YELLOW}[4/5] 等待结果就绪...${C_RESET}"
mkdir -p "$OUTPUT_DIR"
outputFile="${OUTPUT_DIR}/${TASK_NAME}.zip"

for ((i=1; i<=RESULT_MAX_RETRIES; i++)); do
    download_output=$(jcc hpc result download --task-name "$TASK_NAME" --output "$outputFile" 2>&1) && {
        echo -e "${C_GREEN}       文件: ${outputFile}${C_RESET}"
        break
    }

    if [ "$i" -eq "$RESULT_MAX_RETRIES" ]; then
        echo -e "${C_RED}[FAIL] 下载失败（已重试 ${RESULT_MAX_RETRIES} 次）${C_RESET}"
        echo "$download_output"
        exit 1
    fi

    echo -e "${C_DARK_YELLOW}       结果尚未就绪，${RESULT_RETRY_INTERVAL}s 后重试 (${i}/${RESULT_MAX_RETRIES})...${C_RESET}"
    sleep "$RESULT_RETRY_INTERVAL"
done

echo "[5/5] 安全解压到 $OUTPUT_DIR/download ..."
"$PYTHON" -I "$SCRIPT_DIR/hpc_results.py" extract "$outputFile" "$OUTPUT_DIR/download"

# ============ 完成 ============
echo ""
separator "$C_GREEN"
echo -e "${C_GREEN}  全部完成!${C_RESET}"
separator "$C_GREEN"
echo -e "  Package  : ${PACKAGE_NAME} (${packageID})"
echo -e "  Task     : ${TASK_NAME} (${taskID})"
echo -e "  Result   : ${outputFile}"
echo -e "  Extracted: ${OUTPUT_DIR}/download (通常含 workdir/)"
separator "$C_GREEN"
echo ""
