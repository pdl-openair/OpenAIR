#!/bin/bash
#=============================================================================
# MatterGen 带隙协议 (issue #213) HPC 编排脚本
#
# 把论文协议的 5 个串行 VASP 任务通过 jcc CLI 依次提交到 HPCSlurmArm:
#   00 MP-GGA relax 1
#   01 MP-GGA relax 2  ← 吃 step 00 的 CONTCAR
#   02 static          ← 吃 step 01 的 CONTCAR
#   03 non-scf uniform ← 吃 step 02 的 CONTCAR + CHGCAR
#   04 non-scf line    ← 吃 step 02 的 CONTCAR + CHGCAR (不依赖 03)
#
# 输入: 一个 CIF 文件
# 输出: 写入 --output-dir/band_gap.json, 形如 {"<name>": <gap_eV>}
#
# 用法:
#   ./run_bandgap_hpc.sh \
#       --cif /path/to/MoS2-mp-2815.cif \
#       --name MoS2-mp-2815 \
#       --output-dir ./results
#
# 依赖:
#   - jcc CLI, jq, unzip
#   - mattergen venv (.venv/bin/python 含 atomate2)
#   - ~/.pmgrc.yaml 配好 PMG_VASP_PSP_DIR
#   - .env 含 JCC_BASE_URL/JCC_USERNAME/JCC_PASSWORD
#   - 同目录 run_hpc.sh (skill 自带模板)
#=============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 调用兄弟 skill 的单任务模板 (两者同装在 skills/ 目录下, 相对定位; 也可用 RUN_HPC 覆盖)
RUN_HPC="${RUN_HPC:-${SCRIPT_DIR}/../vasp-thermodynamic-stability/scripts/run_hpc.sh}"
# python 解释器 (需含 atomate2/pymatgen/jobflow, 用于生成输入与解析 vasprun.xml; 可用 VENV_PY 覆盖)
VENV_PY="${VENV_PY:-$(command -v python3 || command -v python || true)}"
COMPUTE_SCRIPT="${SCRIPT_DIR}/compute_band_gap.py"

# 颜色
C_CYAN='\033[0;36m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'
C_RED='\033[0;31m'; C_GRAY='\033[0;90m'; C_RESET='\033[0m'

usage() {
    cat <<EOF
用法: $0 --cif <CIF> --name <NAME> --output-dir <DIR> [--nodes N] [--ntasks N]

必选:
  --cif         输入 CIF 文件路径
  --name        结构名称 (用作 task 前缀, 如 MoS2-mp-2815)
  --output-dir  结果根目录 (会创建 inputs/ runs/ final/)

可选:
  --nodes       每个 VASP 任务的节点数 (默认 \$JCC_NODES 或 1)
  --ntasks      每个 VASP 任务的 MPI 进程数 (默认 \$JCC_NTASKS 或 4)
  --skip-relax  跳过 00/01 弛豫 (调试用, 直接从原 POSCAR 跑 static)

环境变量 (从 .env 加载):
  JCC_BASE_URL, JCC_USERNAME, JCC_PASSWORD, JCC_CLUSTER_NAME
EOF
    exit 1
}

# ---------- 参数解析 ----------
CIF=""; NAME=""; OUTPUT_DIR=""; SKIP_RELAX=0
NODES_OPT=""; NTASKS_OPT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --cif) CIF="$2"; shift 2;;
        --name) NAME="$2"; shift 2;;
        --output-dir) OUTPUT_DIR="$2"; shift 2;;
        --nodes) NODES_OPT="--nodes $2"; shift 2;;
        --ntasks) NTASKS_OPT="--ntasks $2"; shift 2;;
        --skip-relax) SKIP_RELAX=1; shift;;
        -h|--help) usage;;
        *) echo "未知参数: $1"; usage;;
    esac
done
[[ -z "$CIF" || -z "$NAME" || -z "$OUTPUT_DIR" ]] && usage
[[ ! -f "$CIF" ]] && { echo "CIF 不存在: $CIF"; exit 1; }
[[ ! -x "$RUN_HPC" ]] && { echo "找不到 run_hpc.sh: $RUN_HPC"; exit 1; }
[[ ! -f "$VENV_PY" ]] && { echo "找不到 venv python: $VENV_PY"; exit 1; }

# ---------- 加载 .env：skill 自身 .env 优先，再从 CWD 向上找作后备；已设变量不被覆盖 ----------
_load_env() {
    local f="$1"
    [[ -f "$f" ]] || return 0
    while IFS='=' read -r k v; do
        case "$k" in ''|\#*) continue ;; esac
        v="${v%\"}"; v="${v#\"}"
        export "$k=${!k:-$v}"
    done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$f")
}
_load_env "$SCRIPT_DIR/../.env"   # skill 自身
if [[ ! -f "$SCRIPT_DIR/../.env" ]]; then
    # skill 无本地 .env 时，从 CWD 向上找项目根后备
    for _d in . .. ../.. ../../.. ../../../..; do
        if [[ -f "$_d/.env" ]]; then _load_env "$_d/.env"; ENV_FILE="$_d/.env"; break; fi
    done
fi
[[ -z "${JCC_BASE_URL:-}" || -z "${JCC_USERNAME:-}" || -z "${JCC_PASSWORD:-}" ]] && {
    echo "错误: 缺少 JCC_BASE_URL/JCC_USERNAME/JCC_PASSWORD (检查 $ENV_FILE)"; exit 1;
}
# .env 里若有 VASP_PSP_DIR (旧别名), 会被 monty 自动覆写 PMG_VASP_PSP_DIR,
# 与 ~/.pmgrc.yaml 中期望的父路径不一致, 这里清掉, 让 pmgrc.yaml 生效.
unset VASP_PSP_DIR PMG_VASP_PSP_DIR PMG_VASP_PSP_SUB_DIRS PMG_DEFAULT_FUNCTIONAL

INPUTS_DIR="$OUTPUT_DIR/inputs"     # compute_band_gap.py 生成的 5 个 job 目录
RUNS_DIR="$OUTPUT_DIR/runs"         # 每步真实上传到云端的目录
FINAL_DIR="$OUTPUT_DIR/final"       # 最终所有产物汇总
mkdir -p "$INPUTS_DIR" "$RUNS_DIR" "$FINAL_DIR"

separator() { echo -e "${C_CYAN}============================================${C_RESET}"; }
echo ""
separator
echo -e "${C_CYAN}  MatterGen Band-Gap Protocol on HPC${C_RESET}"
separator
echo -e "  CIF      : ${CIF}"
echo -e "  Name     : ${NAME}"
echo -e "  Output   : ${OUTPUT_DIR}"
echo -e "  Cluster  : ${JCC_CLUSTER_NAME:-HPCSlurmArm}"
separator
echo ""

# ---------- Phase 0: 用 compute_band_gap.py 生成 5 个 job 的 VASP 输入 ----------
if [[ ! -d "$INPUTS_DIR/$NAME" ]]; then
    echo -e "${C_YELLOW}[Phase 0] 生成 VASP 输入 (compute_band_gap.py --write-inputs)...${C_RESET}"
    "$VENV_PY" "$COMPUTE_SCRIPT" \
        --inputs "$CIF" \
        --output-path "$FINAL_DIR/band_gap.json" \
        --write-inputs "$INPUTS_DIR"
else
    echo -e "${C_GRAY}[Phase 0] 跳过: $INPUTS_DIR/$NAME 已存在${C_RESET}"
fi

# job 目录数组: 按数字前缀顺序
mapfile -t JOB_DIRS < <(find "$INPUTS_DIR/$NAME" -maxdepth 1 -mindepth 1 -type d | sort)
[[ ${#JOB_DIRS[@]} -lt 4 ]] && { echo "错误: 期望至少 4 个 job 目录, 实际 ${#JOB_DIRS[@]}"; exit 1; }
echo -e "${C_GRAY}Jobs: $(printf '%s ' "${JOB_DIRS[@]}")${C_RESET}"

# ---------- 通用: 执行一步云端 VASP ----------
# 参数: step_index, job_dir, task_suffix, prev_dir (可为空)
run_step() {
    local idx="$1" job_dir="$2" suffix="$3" prev_dir="${4:-}"
    local step_dir="$RUNS_DIR/${idx}-${suffix}"
    mkdir -p "$step_dir/upload" "$step_dir/download"

    # Resume: 如果该步已下载过完整产物 (CONTCAR 或 vasprun.xml), 跳过提交.
    # 对 non-scf 步还需要确认 CHGCAR 也已就绪, 因为它们依赖 static 的 CHGCAR.
    local resume_marker=""
    if [[ "$suffix" == *"non-scf"* ]]; then
        resume_marker="$step_dir/download/vasprun.xml"
    else
        resume_marker="$step_dir/download/CONTCAR"
    fi
    if [[ -f "$resume_marker" ]]; then
        echo -e "${C_GRAY}[Phase ${idx}] ${suffix} 已存在 ${resume_marker#, 跳过 (resume)}${C_RESET}"
        return 0
    fi

    # 复制 INCAR/KPOINTS/POTCAR
    cp "$job_dir"/{INCAR,KPOINTS,POTCAR} "$step_dir/upload/"

    # 处理 POSCAR: 第一步用生成的; 其他用上一步 CONTCAR
    if [[ "$idx" -eq 0 || -z "$prev_dir" ]]; then
        cp "$job_dir/POSCAR" "$step_dir/upload/POSCAR"
    else
        # 上一步的 CONTCAR 作为本步 POSCAR
        local prev_contcar="$prev_dir/CONTCAR"
        [[ ! -f "$prev_contcar" ]] && { echo "错误: 上一步无 CONTCAR: $prev_contcar"; exit 1; }
        cp "$prev_contcar" "$step_dir/upload/POSCAR"
        echo -e "${C_GRAY}         POSCAR <- prev CONTCAR${C_RESET}"
    fi

    # non-scf 步需要从 static 复制 CHGCAR
    if [[ "$suffix" == *"non-scf"* ]]; then
        # static 步的 step_dir = RUNS_DIR/2-02_static (idx=2 + suffix=02_static)
        local static_dir="$RUNS_DIR/2-02_static/download"
        local chgcar="$static_dir/CHGCAR"
        if [[ ! -f "$chgcar" ]]; then
            # 兜底: 在 download 树里搜
            chgcar=$(find "$static_dir" -name "CHGCAR" -type f | head -1 || true)
        fi
        [[ ! -f "$chgcar" ]] && { echo "错误: static 步未产出 CHGCAR, non-scf 无法启动"; exit 1; }
        cp "$chgcar" "$step_dir/upload/CHGCAR"
        echo -e "${C_GRAY}         CHGCAR <- static${C_RESET}"
    fi

    echo -e "${C_YELLOW}[Phase ${idx}] 提交 ${suffix} 到 HPC...${C_RESET}"
    # jcc 不允许重名任务, 加随机后缀避免 retry 冲突
    local attempt=""
    local task_name=""
    local zip_file=""
    for attempt in 1 2 3; do
        local rand_suffix=$(head -c 4 /dev/urandom | base32 | tr -dc 'A-Za-z0-9' | head -c 6)
        task_name="${NAME}-${suffix}-${rand_suffix}"
        echo -e "${C_GRAY}         (attempt ${attempt}) task=${task_name}${C_RESET}"
        if "$RUN_HPC" \
                --local-path "$step_dir/upload" \
                --output-dir "$step_dir/download_dir" \
                --task-name "$task_name" \
                $NODES_OPT $NTASKS_OPT; then
            zip_file="$step_dir/download_dir/${task_name}.zip"
            [[ -f "$zip_file" ]] && break
        fi
        echo -e "${C_YELLOW}         attempt ${attempt} 失败, 5s 后重试...${C_RESET}"
        rm -rf "$step_dir/download_dir"
        sleep 5
    done
    [[ -z "$zip_file" || ! -f "$zip_file" ]] && { echo "错误: ${suffix} 三次重试均失败"; exit 1; }

    unzip -o -q "$zip_file" -d "$step_dir/download"

    # 服务端打包会把 VASP 输出放在 workdir/ 子目录下, 提到 download/ 顶层
    # 方便后续 step 用 $prev_dir/CONTCAR 直接定位.
    if [[ -d "$step_dir/download/workdir" ]]; then
        mv "$step_dir/download/workdir/"* "$step_dir/download/" 2>/dev/null || true
        rmdir "$step_dir/download/workdir" 2>/dev/null || true
    fi

    # 把关键产物拷到 FINAL_DIR (最后一次的 vasprun.xml 最重要)
    if [[ "$suffix" == "non-scf_line" ]]; then
        cp -r "$step_dir/download/"* "$FINAL_DIR/" 2>/dev/null || true
    fi

    echo -e "${C_GREEN}[Phase ${idx}] ${suffix} 完成${C_RESET}\n"
}

# ---------- 依次执行 5 步 ----------
STEPS=("00_MP_GGA_relax_1" "01_MP_GGA_relax_2" "02_static" "03_non-scf_uniform" "04_non-scf_line")

if [[ "$SKIP_RELAX" -eq 1 ]]; then
    echo -e "${C_YELLOW}[--skip-relax] 跳过 00/01, 直接从 static 开始${C_RESET}"
    run_step 2 "${JOB_DIRS[2]}" "02_static" ""
    run_step 3 "${JOB_DIRS[3]}" "03_non-scf_uniform" "$RUNS_DIR/2-02_static/download"
    run_step 4 "${JOB_DIRS[4]}" "04_non-scf_line" "$RUNS_DIR/2-02_static/download"
else
    run_step 0 "${JOB_DIRS[0]}" "00_MP_GGA_relax_1" ""
    run_step 1 "${JOB_DIRS[1]}" "01_MP_GGA_relax_2" "$RUNS_DIR/0-00_MP_GGA_relax_1/download"
    run_step 2 "${JOB_DIRS[2]}" "02_static" "$RUNS_DIR/1-01_MP_GGA_relax_2/download"
    run_step 3 "${JOB_DIRS[3]}" "03_non-scf_uniform" "$RUNS_DIR/2-02_static/download"
    run_step 4 "${JOB_DIRS[4]}" "04_non-scf_line" "$RUNS_DIR/2-02_static/download"
fi

# ---------- Phase 5: 解析带隙 ----------
echo -e "${C_YELLOW}[Phase 5] 解析带隙...${C_RESET}"
"$VENV_PY" - <<PY
import json, sys
from pathlib import Path
from pymatgen.io.vasp import Vasprun

final = Path("$FINAL_DIR")
vr_path = next(final.rglob("vasprun.xml"), None)
if vr_path is None:
    print("错误: $FINAL_DIR 下未找到 vasprun.xml", file=sys.stderr)
    sys.exit(1)
vr = Vasprun(str(vr_path), parse_potcar_file=False)
gap = vr.eigenvalue_band_properties[0] if vr.eigenvalue_band_properties else None
out = { "$NAME": gap }
(final / "band_gap.json").write_text(json.dumps(out, indent=2))
print(f"band_gap_eV = {gap}")
PY

separator
echo -e "${C_GREEN}  全部完成!${C_RESET}"
echo -e "  band_gap.json: $FINAL_DIR/band_gap.json"
separator
echo ""
cat "$FINAL_DIR/band_gap.json"
echo ""
