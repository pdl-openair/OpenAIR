#!/bin/bash
#=============================================================================
# 为所有竞争相生成 POTCAR 文件
#
# 前提: 需要设置 VASP_PSP_DIR 环境变量, 或使用 --psp-dir 指定赝势目录
#       赝势目录结构: $PSP_DIR/<Element>/POTCAR
#
# 用法:
#   ./generate_all_potcar.sh                         # 使用 VASP_PSP_DIR
#   ./generate_all_potcar.sh --psp-dir /path/to/potpaw_PBE
#=============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 默认赝势目录 (必须通过环境变量或参数提供, 不做任何硬编码假设)
PSP_DIR="${VASP_PSP_DIR:-}"

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --psp-dir)
            PSP_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "用法: $0 [--psp-dir <路径>]"
            echo ""
            echo "环境变量:"
            echo "  VASP_PSP_DIR  赝势根目录 (结构: \$VASP_PSP_DIR/<Element>/POTCAR)"
            exit 0
            ;;
        *)
            echo "未知参数: $1"
            echo "用法: $0 [--psp-dir <路径>]"
            exit 1
            ;;
    esac
done

# 检查赝势目录
if [ -z "$PSP_DIR" ]; then
    echo "ERROR: 未指定赝势目录"
    echo "请设置 VASP_PSP_DIR 环境变量或使用 --psp-dir 指定"
    echo ""
    echo "示例:"
    echo "  export VASP_PSP_DIR=/path/to/vasp/pseudopotentials"
    echo "  $0"
    echo "  $0 --psp-dir /path/to/potpaw_PBE"
    exit 1
fi

if [ ! -d "$PSP_DIR" ]; then
    echo "ERROR: 赝势目录不存在: $PSP_DIR"
    exit 1
fi

echo "PSP_DIR = $PSP_DIR"
echo ""

# 读取 submission_info.json 中的元素信息
SUBMISSION_INFO="$SCRIPT_DIR/submission_info.json"
if [ ! -f "$SUBMISSION_INFO" ]; then
    echo "ERROR: 未找到 $SUBMISSION_INFO"
    echo "请先运行 setup_competing_phases.py 生成竞争相目录"
    exit 1
fi

# 动态收集所有需要的元素 (从 submission_info.json, 不写死任何元素)
REQUIRED_ELEMENTS=$(python3 -c "
import json
with open('$SUBMISSION_INFO') as f:
    elements = set()
    for item in json.load(f):
        elements.update(item['elements'])
    print(' '.join(sorted(elements)))
")

echo "需要的元素: $REQUIRED_ELEMENTS"
echo ""

# 检查必需的元素赝势
for el in $REQUIRED_ELEMENTS; do
    if [ ! -f "$PSP_DIR/$el/POTCAR" ]; then
        echo "ERROR: 缺少 $el 赝势: $PSP_DIR/$el/POTCAR"
        exit 1
    fi
done

# 为每个相生成 POTCAR
total=$(python3 -c "import json; print(len(json.load(open('$SUBMISSION_INFO'))))")

count=0
python3 -c "
import json
with open('$SUBMISSION_INFO') as f:
    for item in json.load(f):
        print(item['dirname'], ' '.join(item['elements']))
" | while read -r dirname elements; do
    count=$((count + 1))

    phase_dir="$SCRIPT_DIR/$dirname"
    relax_dir="$phase_dir/relax"
    static_dir="$phase_dir/static"

    # 构建 cat 命令
    cat_cmd="cat"
    for el in $elements; do
        cat_cmd="$cat_cmd $PSP_DIR/$el/POTCAR"
    done

    # 生成 relax/POTCAR
    eval "$cat_cmd" > "$relax_dir/POTCAR"
    # 生成 static/POTCAR (相同)
    eval "$cat_cmd" > "$static_dir/POTCAR"

    echo "  [$count/$total] $dirname: POTCAR generated ($elements)"
done

echo ""
echo "所有 POTCAR 已生成。"
echo ""
echo "验证:"
for d in "$SCRIPT_DIR"/*/relax/POTCAR; do
    [ -f "$d" ] || continue
    nlines=$(wc -l < "$d")
    echo "  $(dirname "$d" | xargs basename)/relax/POTCAR: $nlines lines"
done
