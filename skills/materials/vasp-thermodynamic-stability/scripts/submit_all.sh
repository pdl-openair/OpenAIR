#!/bin/bash
#=============================================================================
# 批量提交竞争相 VASP 计算到 HPC
#
# 工作流:
#   Phase 1: 提交所有结构的弛豫计算 (relax/)
#   Phase 2: 弛豫完成后, 用 CONTCAR 更新 static/POSCAR, 提交静态计算
#   Phase 3: 全部完成后, 运行 analyze_stability.py 分析结果
#
# 用法:
#   ./submit_all.sh relax           # 提交所有弛豫计算
#   ./submit_all.sh static          # 弛豫完成后提交静态计算
#   ./submit_all.sh collect         # 从 HPC 结果中提取 CONTCAR 和 OUTCAR
#   ./submit_all.sh status          # 查看所有任务状态
#   ./submit_all.sh all             # relax + collect + static + analyze (全自动)
#=============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 指向 skill 内置的 run_hpc.sh 模板 (同目录); 也可通过环境变量 RUN_HPC 覆盖
RUN_HPC="${RUN_HPC:-$SCRIPT_DIR/run_hpc.sh}"
RESULTS_BASE="$SCRIPT_DIR/results"

# ── 加载 .env：skill 自身目录优先，已设的变量不被覆盖 ──
_load_env() {
  local f="$1"; [ -f "$f" ] || return 0
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue ;; esac
    v="${v%\"}"; v="${v#\"}"
    export "$k=${!k:-$v}"
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$f")
}
_load_env "$SCRIPT_DIR/../.env"          # skill 自身
# 项目根后备：从脚本位置（或拷贝后的工作目录）向上逐级找 .env（跳过已加载的 skill 自身）
_d="$SCRIPT_DIR"
for _ in 1 2 3 4 5 6; do
  _d="$(dirname "$_d")"
  [ "$_d" = "$SCRIPT_DIR/.." ] && continue
  [ -f "$_d/.env" ] && { _load_env "$_d/.env"; break; }
done
unset _d

# 颜色
C_GREEN='\033[0;32m'
C_YELLOW='\033[0;33m'
C_CYAN='\033[0;36m'
C_RED='\033[0;31m'
C_RESET='\033[0m'

# 获取所有相目录 (排除非相目录)
get_phase_dirs() {
    for d in "$SCRIPT_DIR"/*/relax; do
        if [ -d "$d" ]; then
            dirname "$(dirname "$d")"
        fi
    done | sort
}

# ========== 提交弛豫计算 ==========
submit_relax() {
    echo -e "${C_CYAN}============================================${C_RESET}"
    echo -e "${C_CYAN}  Phase 1: 提交弛豫计算${C_RESET}"
    echo -e "${C_CYAN}============================================${C_RESET}"

    local count=0
    local total=0

    # 统计总数
    for phase_dir in "$SCRIPT_DIR"/*/relax; do
        [ -d "$phase_dir" ] || continue
        total=$((total + 1))
    done

    for phase_dir in "$SCRIPT_DIR"/*/relax; do
        [ -d "$phase_dir" ] || continue
        phase_name=$(basename "$(dirname "$phase_dir")")

        # 检查是否已有结果
        local result_dir="$RESULTS_BASE/${phase_name}_relax"
        if [ -f "$result_dir/CONTCAR" ] || [ -f "$result_dir/download/workdir/CONTCAR" ] || [ -f "$result_dir/download/CONTCAR" ]; then
            echo -e "${C_GREEN}  [$((count+1))/$total] SKIP $phase_name (已有结果)${C_RESET}"
            count=$((count + 1))
            continue
        fi

        echo -e "${C_YELLOW}  [$((count+1))/$total] SUBMIT $phase_name ...${C_RESET}"

        # 检查 POTCAR
        if [ ! -f "$phase_dir/POTCAR" ]; then
            echo -e "${C_RED}    ERROR: 缺少 POTCAR, 请先生成: $phase_dir/POTCAR${C_RESET}"
            echo -e "${C_RED}    提示: 在各相目录运行 generate_potcar.sh${C_RESET}"
            count=$((count + 1))
            continue
        fi

        # 调用 run_hpc.sh
        local output_dir="$RESULTS_BASE/${phase_name}_relax"
        mkdir -p "$output_dir"

        if bash "$RUN_HPC" --local-path "$phase_dir" --output-dir "$output_dir" --task-name "${phase_name}_relax"; then
            echo -e "${C_GREEN}    OK: 结果保存到 $output_dir${C_RESET}"
        else
            echo -e "${C_RED}    FAIL: $phase_name 提交失败${C_RESET}"
        fi

        count=$((count + 1))
    done

    echo ""
    echo -e "${C_GREEN}弛豫提交完成: $count/$total${C_RESET}"
}

# ========== 收集弛豫结果, 更新 static POSCAR ==========
collect_and_prepare_static() {
    echo -e "${C_CYAN}============================================${C_RESET}"
    echo -e "${C_CYAN}  Phase 2: 收集弛豫结果并准备静态计算${C_RESET}"
    echo -e "${C_CYAN}============================================${C_RESET}"

    local count=0
    local ready=0

    for result_dir in "$RESULTS_BASE"/*_relax; do
        [ -d "$result_dir" ] || continue
        phase_name=$(basename "$result_dir" | sed 's/_relax$//')
        phase_dir="$SCRIPT_DIR/$phase_name"
        static_dir="$phase_dir/static"

        count=$((count + 1))

        # 查找下载的 zip 文件
        local zip_file=$(find "$result_dir" -name "*.zip" -type f 2>/dev/null | head -1)

        if [ -n "$zip_file" ]; then
            # 解压
            local workdir="$result_dir/download"
            if [ ! -d "$workdir" ]; then
                "${MATERIALS_PY:-${EVOLVE_PY:-python}}" -I "$SCRIPT_DIR/hpc_results.py" extract "$zip_file" "$workdir"
            fi

            # 查找 CONTCAR
            local contcar=$(find "$workdir" -name "CONTCAR" -type f | head -1)
            if [ -n "$contcar" ]; then
                cp "$contcar" "$static_dir/POSCAR"
                echo -e "${C_GREEN}  [$count] $phase_name: CONTCAR -> static/POSCAR${C_RESET}"
                ready=$((ready + 1))

                # 同时保存能量信息
                local outcar=$(find "$workdir" -name "OUTCAR" -type f | head -1)
                if [ -n "$outcar" ]; then
                    grep "FREE ENERGIE" -A 4 "$outcar" | tail -3 > "$result_dir/energy.txt"
                fi
            else
                echo -e "${C_RED}  [$count] $phase_name: zip 中未找到 CONTCAR${C_RESET}"
            fi
        else
            # 可能已经手动解压
            local contcar=$(find "$result_dir" -name "CONTCAR" -type f | head -1)
            if [ -n "$contcar" ]; then
                cp "$contcar" "$static_dir/POSCAR"
                echo -e "${C_GREEN}  [$count] $phase_name: CONTCAR -> static/POSCAR${C_RESET}"
                ready=$((ready + 1))
            else
                echo -e "${C_YELLOW}  [$count] $phase_name: 无结果${C_RESET}"
            fi
        fi
    done

    echo ""
    echo -e "${C_GREEN}准备就绪: $ready/$count 可提交静态计算${C_RESET}"
}

# ========== 提交静态计算 ==========
submit_static() {
    echo -e "${C_CYAN}============================================${C_RESET}"
    echo -e "${C_CYAN}  Phase 3: 提交静态计算${C_RESET}"
    echo -e "${C_CYAN}============================================${C_RESET}"

    local count=0
    local total=0

    for phase_dir in "$SCRIPT_DIR"/*/static; do
        [ -d "$phase_dir" ] || continue
        total=$((total + 1))
    done

    for phase_dir in "$SCRIPT_DIR"/*/static; do
        [ -d "$phase_dir" ] || continue
        phase_name=$(basename "$(dirname "$phase_dir")")

        count=$((count + 1))

        # 检查 POSCAR 是否已更新 (检查文件修改时间)
        if [ ! -f "$phase_dir/POSCAR" ]; then
            echo -e "${C_RED}  [$count/$total] SKIP $phase_name: 无 POSCAR${C_RESET}"
            continue
        fi

        # 检查是否已有静态结果
        local result_dir="$RESULTS_BASE/${phase_name}_static"
        if [ -f "$result_dir/energy.txt" ] || [ -f "$result_dir/download/workdir/OUTCAR" ] || [ -f "$result_dir/download/OUTCAR" ]; then
            echo -e "${C_GREEN}  [$count/$total] SKIP $phase_name (已有静态结果)${C_RESET}"
            continue
        fi

        # 检查 POTCAR
        if [ ! -f "$phase_dir/POTCAR" ]; then
            echo -e "${C_RED}  [$count/$total] SKIP $phase_name: 缺少 POTCAR${C_RESET}"
            continue
        fi

        echo -e "${C_YELLOW}  [$count/$total] SUBMIT $phase_name static ...${C_RESET}"

        mkdir -p "$result_dir"

        if bash "$RUN_HPC" --local-path "$phase_dir" --output-dir "$result_dir" --task-name "${phase_name}_static"; then
            echo -e "${C_GREEN}    OK${C_RESET}"
        else
            echo -e "${C_RED}    FAIL${C_RESET}"
        fi
    done

    echo ""
    echo -e "${C_GREEN}静态计算提交完成: $count/$total${C_RESET}"
}

# ========== 提取所有静态计算能量 ==========
collect_energies() {
    echo -e "${C_CYAN}============================================${C_RESET}"
    echo -e "${C_CYAN}  提取所有静态计算能量${C_RESET}"
    echo -e "${C_CYAN}============================================${C_RESET}"

    "${MATERIALS_PY:-${EVOLVE_PY:-python}}" -I "$SCRIPT_DIR/hpc_results.py" energies "$SCRIPT_DIR"
    echo "能量保存到 $SCRIPT_DIR/energies.json (cell eV + OUTCAR NIONS)"
}

# ========== 主逻辑 ==========
case "${1:-help}" in
    relax)
        submit_relax
        ;;
    collect)
        collect_and_prepare_static
        ;;
    static)
        submit_static
        ;;
    energies)
        collect_energies
        ;;
    status)
        echo -e "${C_CYAN}当前状态:${C_RESET}"
        echo ""
        echo "弛豫结果:"
        for d in "$RESULTS_BASE"/*_relax; do
            [ -d "$d" ] || continue
            name=$(basename "$d")
            if find "$d" -name "CONTCAR" -type f | head -1 > /dev/null 2>&1; then
                echo -e "  ${C_GREEN}[OK]${C_RESET} $name"
            else
                echo -e "  ${C_YELLOW}[--]${C_RESET} $name"
            fi
        done
        echo ""
        echo "静态结果:"
        for d in "$RESULTS_BASE"/*_static; do
            [ -d "$d" ] || continue
            name=$(basename "$d")
            if find "$d" -name "OUTCAR" -type f | head -1 > /dev/null 2>&1; then
                echo -e "  ${C_GREEN}[OK]${C_RESET} $name"
            else
                echo -e "  ${C_YELLOW}[--]${C_RESET} $name"
            fi
        done
        ;;
    all)
        echo -e "${C_CYAN}全自动模式: relax -> collect -> static -> energies${C_RESET}"
        submit_relax
        collect_and_prepare_static
        submit_static
        collect_energies
        echo ""
        echo -e "${C_GREEN}全部完成! 运行 analyze_stability.py 构建相图${C_RESET}"
        ;;
    *)
        echo "用法: $0 {relax|collect|static|energies|status|all}"
        echo ""
        echo "  relax    - 提交所有弛豫计算"
        echo "  collect  - 收集弛豫结果, 更新 static/POSCAR"
        echo "  static   - 提交所有静态计算"
        echo "  energies - 提取所有静态计算能量"
        echo "  status   - 查看任务状态"
        echo "  all      - 全自动执行所有步骤"
        ;;
esac
