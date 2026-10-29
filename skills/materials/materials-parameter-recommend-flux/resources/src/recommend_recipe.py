"""
Multi-Scheme Recipe Generation System

This module generates experimental crystal growth recipes using a two-stage LLM pipeline:
1. Stage 1: Analyzes similar materials' recipes to extract typical growth schemes
2. Stage 2: Generates multiple recipe variations based on parameter windows and user preferences

Author: Claude Code
Date: 2026-02-07

Usage:
    python -m src.recommend_recipe \
        --similar-file data/similar_mates/similar_on-base_processed.jsonl \
        --window-file data/recommand_window/output_result.jsonl \
        --input-file data/recommand_recipe/input_real.jsonl \
        --output-file data/recommand_recipe/output_result.jsonl \
        --feature-output data/recommand_recipe/similar_recipes_feat_result.jsonl
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Any

# Import LLM API and JSON parsing utilities
import os

# Select LLM API implementation at runtime based on environment variable
# If USE_MATAI_API is set to true/1/yes, try to use utils.matai_api.get_chat_response,
# otherwise fall back to src.reference_api.get_chat_response.
_USE_MATAI = str(os.getenv("USE_MATAI_API", "0")).lower() in ("1", "true", "yes", "on")
_USE_CACHE = str(os.getenv("USE_CACHE", "0")).lower() in ("1", "true", "yes", "on")
if _USE_MATAI:
    try:
        from utils.matai_api import get_chat_response
    except Exception:
        from src.reference_api import get_chat_response
else:
    from src.reference_api import get_chat_response
from utils.response2json import response2json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# Data Loading Functions
# ============================================================================

def strip_json_comments(content: str) -> str:
    """
    Strip // comments from JSON content.

    Args:
        content: JSON string with comments

    Returns:
        JSON string without comments
    """
    lines = []
    for line in content.split('\n'):
        # Remove // comments (but preserve strings with //)
        if '//' in line:
            # Find // outside of strings
            in_string = False
            escape = False
            for i, char in enumerate(line):
                if escape:
                    escape = False
                    continue
                if char == '\\':
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                if char == '/' and i + 1 < len(line) and line[i + 1] == '/' and not in_string:
                    line = line[:i]
                    break
        lines.append(line)
    return '\n'.join(lines)


def load_jsonl(file_path: str) -> Dict:
    """
    Load single JSON object from file (handles both JSONL and pretty-printed JSON).
    Strips // comments before parsing.

    Args:
        file_path: Path to JSON/JSONL file

    Returns:
        Dictionary loaded from file
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                logger.error(f"Empty file: {file_path}")
                sys.exit(1)

            # Strip comments
            content = strip_json_comments(content)

            return json.loads(content)
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {file_path}: {e}")
        sys.exit(1)


# ============================================================================
# Retry Logic
# ============================================================================

def retry_llm_call(prompt: str, system_prompt: str, max_retries: int = 3) -> str:
    """
    Retry LLM call up to max_retries times with exponential backoff.

    Args:
        prompt: User prompt
        system_prompt: System prompt
        max_retries: Maximum number of retry attempts

    Returns:
        LLM response string
    """
    # print(prompt)
    # input("Press Enter to send prompt to LLM...")
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"LLM call attempt {attempt}/{max_retries}")
            response = get_chat_response(
                prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                think_budget=8192,
                max_tokens=16384
            )

            if response and response.strip():
                logger.info(f"LLM call succeeded on attempt {attempt}")
                return response
            else:
                logger.warning(f"Attempt {attempt}: Empty response")

        except Exception as e:
            logger.warning(f"Attempt {attempt} failed: {e}")

        if attempt < max_retries:
            sleep_time = 2 ** attempt  # Exponential backoff: 2s, 4s, 8s
            logger.info(f"Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)

    logger.error("All retry attempts failed")
    sys.exit(1)


# ============================================================================
# Stage 1: Feature Extraction
# ============================================================================

def build_feature_extraction_prompt(similar_recipes: Dict) -> str:
    """
    Build prompt for LLM to extract typical growth schemes from similar recipes.

    Args:
        similar_recipes: Dict containing target material and similar materials list

    Returns:
        Formatted prompt string
    """
    target_material = similar_recipes.get("目标材料", {})
    similar_materials = similar_recipes.get("相似材料列表", [])

    # Extract all recipes from similar materials
    all_recipes = []
    for material in similar_materials:
        material_id = material.get("材料ID", "unknown")
        material_formula = material.get("化学式", "unknown")
        recipes = material.get("配方列表", [])

        for recipe in recipes:
            recipe_id = recipe.get("配方ID", "unknown")
            recipe_info = {
                "配方ID": recipe_id,
                "材料ID": material_id,
                "材料ID-化学式": f"{material_id}-{material_formula}",
                "材料": material_formula,
                "助熔剂比例": recipe.get("工艺配方", {}).get("助熔剂_对_溶质_摩尔比", "N/A"),
                "Tmax": recipe.get("温度程序", {}).get("最高温段保温温度_摄氏", "N/A"),
                "降温速率": recipe.get("温度程序", {}).get("降温速率_主降温_℃每小时", "N/A"),
                "保温时间": recipe.get("温度程序", {}).get("最高温段保温时间_h", "N/A"),
                "分离方式": recipe.get("分离与后处理", {}).get("分离方式", "N/A"),
                "容器": recipe.get("工艺配方", {}).get("容器", "N/A"),
            }
            all_recipes.append(recipe_info)

    # Build recipe list string
    recipe_list_str = ""
    for i, recipe in enumerate(all_recipes, 1):
        recipe_list_str += f"[{i}] 配方ID: {recipe['配方ID']} | 材料ID-化学式: {recipe['材料ID-化学式']} | 助熔剂比例: {recipe['助熔剂比例']} | Tmax: {recipe['Tmax']}℃ | 降温速率: {recipe['降温速率']}℃/h | 保温时间: {recipe['保温时间']}h | 分离方式: {recipe['分离方式']} | 容器: {recipe['容器']}\n"

    prompt = f"""任务：分析以下相似材料的配方，从5个维度提炼典型生长方案。

目标材料：
- 化学式: {target_material.get('化学式', 'N/A')}
- 结构原型: {target_material.get('结构原型', 'N/A')}
- 材料族系: {target_material.get('材料族系', [])}

相似配方列表（共 {len(all_recipes)} 条）：
{recipe_list_str}

分析维度：
1. 助熔剂比例：高稀释(>10) / 中等(2-10) / 低(<2) / 自助熔剂
2. 温度程序：Tmax范围、保温时长、降温速率
3. 生长策略：慢冷大晶体 / 快冷小晶体 / 高温长保温 / 低温保守
4. 分离方式：离心 / 酸洗 / 倾析 / 机械分离
5. 材料适配性：适用的结构类型、族系、维度特征

输出要求：
1. 识别 5-7 种典型生长方案
2. 每种方案必须包含：
   - 方案ID（例如：scheme_001）
   - 方案名称（简洁描述，例如：高助熔剂稀释方案）
   - 核心特征（参数范围和规律）
   - 适用（适用场景和材料类型）
   - 代表配方ID列表（至少3个，从上述配方列表中选择）
   - 代表配方对应材料ID-化学式列表（从配方列表中复制记录代表配方的材料ID-化学式项）

输出格式：严格的JSON，包含两个字段：
{{
  "配方特征": [
    {{
      "配方ID": "rec_001",
      "对应材料ID-化学式": "mat_001-Eu2AuGe3",
      "助熔剂比例": "高稀释",
      "温度程序": {{"最高温度_摄氏": 1000, "降温速率__℃每小时": 10, "保温时间_h": 24}},
      "生长策略": "缓慢降温",
      "分离方式": "酸洗",
      "材料适配性": "适用于MoS2类材料"
    }}
  ],
  "典型生长方案": [
    {{
      "方案ID": "scheme_001",
      "方案名称": "高助熔剂稀释方案",
      "核心特征": "助熔剂 / 溶质摩尔比显著偏高（多数 > 5，最高达 25.9），溶质高度稀释；降温速率中速偏快（5~20℃/h），Tmax 以中温为主",
      "适用": "易偏析、多相竞争、难溶的稀土金属间化合物、拓扑材料、铁基超导，抑制杂相、提高单晶纯度",
      "代表配方ID列表": ["rec_001", "rec_002", "rec_003"],
      "对应材料ID-化学式列表": ["mat_001-Eu2AuGe3", "mat_002-(Pb, La)(Zr, Sn, Ti)O3", "mat_003-MoS2"]
    }}
  ]
}}

注意：
1. 配方特征中必须包含"对应材料ID-化学式"字段，格式为"材料ID-化学式"（例如："mat_001-Eu2AuGe3"）
2. 典型生长方案必须包含"方案ID"字段，格式为"scheme_001"、"scheme_002"等

不要输出markdown代码块，直接输出JSON。"""

    return prompt


def generate_feature_summary(similar_recipes: Dict) -> Dict:
    """
    Stage 1: Generate feature summary from similar recipes.

    Args:
        similar_recipes: Dict containing target material and similar materials list

    Returns:
        Dict with 配方特征 and 典型生长方案
    """
    logger.info("Stage 1: Generating feature summary from similar recipes")

    # Build prompt
    prompt = build_feature_extraction_prompt(similar_recipes)

    # Call LLM with retry
    system_prompt = "你是材料晶体生长工艺专家，擅长从大量配方中提炼典型方案规律。"

    ## way1：让LLM推理典型方案规律
    ## way2：从cache中提取典型方案规律
    # print("debug==============\n", similar_recipes[:50])
    material_id_formula = similar_recipes.get("目标材料", {}).get("化学式", "unknown")

    if _USE_CACHE:
        logger.info(f"Using cache for feature summary: {material_id_formula}")
        cache_dir = Path(__file__).parent.parent / 'data' / 'recommand_recipe'
        cache_file = cache_dir / f'similar_recipes_feat_result-{material_id_formula}.jsonl'
        with open(cache_file, 'r', encoding='utf-8') as f:
            feature_summary = json.load(f)
    else:
        logger.info("Using LLM for feature summary")
        response = retry_llm_call(prompt, system_prompt)
        feature_summary = response2json(response)

    if not feature_summary:
        print(response)
        logger.error("Failed to parse feature summary JSON")
        sys.exit(1)

    # Validate structure
    if "配方特征" not in feature_summary or "典型生长方案" not in feature_summary:
        logger.error("Feature summary missing required fields: 配方特征 or 典型生长方案")
        sys.exit(1)

    logger.info(f"Feature summary generated: {len(feature_summary.get('典型生长方案', []))} typical schemes identified")

    return feature_summary


# ============================================================================
# Stage 2: Recipe Generation
# ============================================================================

def build_baseline_scheme(param_windows: Dict, target_material: Dict) -> Dict:
    """
    Build baseline scheme using recommended default values from parameter windows.

    Args:
        param_windows: Dict containing parameter windows
        target_material: Dict containing target material info

    Returns:
        Dict representing baseline scheme
    """
    logger.info("Building baseline scheme from parameter windows")

    # Extract parameter windows and lab constraints
    windows = param_windows.get("参数窗口", {})
    lab_constraints = param_windows.get("实验室约束", {})

    # Helper function to get recommended value or mode
    def get_recommended_value(param_name: str, default: Any = "Not specified") -> Any:
        param_data = windows.get(param_name, {})
        param_type = param_data.get("参数类型", "")

        if param_type == "数值":
            return param_data.get("推荐默认值", default)
        elif param_type == "类别":
            # Get most frequent value (highest 加权支持度)
            candidates = param_data.get("候选取值列表", [])
            if candidates:
                # Sort by 加权支持度 descending
                sorted_candidates = sorted(candidates, key=lambda x: x.get("加权支持度", 0), reverse=True)
                return sorted_candidates[0].get("取值", default)
        return default

    # Helper: try to get minimum boiling point (摄氏) from target material or approximate by formula
    def _min_boiling_point_from_target(tm: Dict) -> Any:
        # If precomputed field exists, prefer it
        for key in ("制备元素最低沸点_摄氏", "元素最低沸点_摄氏", "最低沸点_摄氏"):
            if key in tm:
                try:
                    return float(tm[key])
                except Exception:
                    pass

        # Fallback: parse chemical formula to elements and use a small lookup table
        formula = tm.get("化学式", "")
        if not formula:
            return None

        import re

        # basic element boiling points (摄氏)，覆盖常见元素
        boiling_points = {
            'H': -253.0, 'He': -269.0, 'Li': 1342.0, 'Be': 2470.0, 'B': 4000.0,
            'C': 4827.0, 'N': -196.0, 'O': -183.0, 'F': -188.0, 'Na': 883.0,
            'Mg': 1090.0, 'Al': 2470.0, 'Si': 2900.0, 'P': 280.0, 'S': 444.0,
            'Cl': -34.0, 'K': 759.0, 'Ca': 1484.0, 'Sc': 2830.0, 'Ti': 3287.0,
            'V': 3407.0, 'Cr': 2672.0, 'Mn': 2061.0, 'Fe': 2862.0, 'Co': 2927.0,
            'Ni': 2913.0, 'Cu': 2562.0, 'Zn': 907.0, 'Ga': 2204.0, 'Ge': 2830.0,
            'As': 613.0, 'Se': 684.0, 'Br': 59.0, 'Rb': 688.0, 'Sr': 1382.0,
            'Y': 3336.0, 'Zr': 4409.0, 'Nb': 4744.0, 'Mo': 4612.0, 'Ru': 3909.0,
            'Rh': 3695.0, 'Pd': 2963.0, 'Ag': 2162.0, 'Cd': 767.0, 'In': 2072.0,
            'Sn': 2602.0, 'Sb': 1587.0, 'Te': 988.0, 'I': 184.0, 'Cs': 671.0,
            'Ba': 1870.0, 'La': 3464.0, 'Ce': 3443.0, 'Pb': 1749.0, 'Bi': 1564.0,
            'U': 4131.0
        }

        elems = re.findall(r'([A-Z][a-z]?)', formula)
        bps = []
        for e in elems:
            if e in boiling_points:
                bps.append(boiling_points[e])

        if bps:
            return min(bps)
        return None

    # Determine allowed maximum temperature from constraints
    lab_max_temp = None
    try:
        lab_max_temp = float(lab_constraints.get('最高允许温度_摄氏')) if '最高允许温度_摄氏' in lab_constraints else None
    except Exception:
        lab_max_temp = None

    elem_min_bp = _min_boiling_point_from_target(target_material)

    # final allowed max: take the minimum of available constraints
    allowed_max_temp = None
    candidates = [t for t in (lab_max_temp, elem_min_bp) if t is not None]
    if candidates:
        allowed_max_temp = min(candidates)

    baseline = {
        "方案ID": "方案_A",
        "方案类型": "baseline",
        "方案给人的一句话说明": "中规中矩方案：取各参数窗口中值，参考多数成功配方的典型条件。",
        "工艺参数": {
            "原料信息": f"按化学计量配比（目标材料：{target_material.get('化学式', 'N/A')}）",
            "原料标准化": f"//原料信息中元素符号的化学计量配比，如Al:In:Se=1:1:3",
            "助熔剂信息": f"助熔剂，约 {get_recommended_value('助熔剂_对_溶质_摩尔比', 5)} 倍摩尔过量（窗口推荐值）",
            "助熔剂标准化": f"//助熔剂信息中助熔剂与溶质的摩尔比，如Sn:AlInS3={get_recommended_value('助熔剂_对_溶质_摩尔比', 5)}:1 ",
            "容器": get_recommended_value("容器", "Al2O3 坩埚"),
            "籽晶": get_recommended_value("籽晶", "无"),
            "温度程序": {
                "是否存在次高温预反应段": get_recommended_value("是否存在次高温预反应段", "否"),
                "升温到次高温时间_h": get_recommended_value("升温到次高温时间_h", 10.0),
                "次高温段温度_摄氏": get_recommended_value("次高温段温度_摄氏", 600.0),
                "次高温段保温时间_h": get_recommended_value("次高温段保温时间_h", 12.0),
                "升温到最高温时间_h": get_recommended_value("升温到最高温时间_h", 20.0),
                "最高温段保温温度_摄氏": get_recommended_value("最高温段保温温度_摄氏", 950.0),
                "最高温段保温时间_h": get_recommended_value("最高温段保温时间_h", 24.0),
                "降温速率_主降温_℃每小时": get_recommended_value("降温速率_主降温_℃每小时", 2.0),
                "降温时间_主降温_h": get_recommended_value("降温时间_主降温_h", 100.0),
                "低温段保温温度_摄氏": get_recommended_value("低温段保温温度_摄氏", 700.0),
                "低温段保温时间_h": get_recommended_value("低温段保温时间_h", 24.0),
                "冷却速率_至室温_标签": get_recommended_value("冷却速率_至室温_标签", "炉冷")
            },
            "分离与后处理": {
                "分离方式": get_recommended_value("分离方式", "离心"),
                "分离温度_摄氏": get_recommended_value("分离温度_摄氏", 600.0),
                "晶体的进一步处理": get_recommended_value("晶体的进一步处理", "无需额外退火")
            }
        },
        "预期结果标签": {
            "预期晶体尺寸": "mm 级大概率",
            "预期风险水平": "中等偏低",
            "风险来源简述": [
                "参数接近相似材料配方均值，保守程度较高。",
                "未对 Tmax 做额外提高，热分解风险较低。"
            ]
        },
        "溯源信息": {
            "主要参考配方ID": [],
            "参考材料ID-化学式": [],
            "参考方案类型ID-名称": []
        }
    }

    # Enforce temperature constraints on baseline Tmax and 次高温段
    try:
        # 次高温段温度约束：不得超过元素沸点（用于反应和晶体生长）
        th = baseline['工艺参数']['温度程序'].get('次高温段温度_摄氏')
        th_val = None
        if isinstance(th, (int, float)):
            th_val = float(th)
        else:
            try:
                th_val = float(str(th))
            except Exception:
                th_val = None

        if allowed_max_temp is not None and th_val is not None:
            # keep small margin (5 ℃) below the strict limit to be conservative
            safe_limit = allowed_max_temp - 5.0
            if safe_limit <= 0:
                safe_limit = allowed_max_temp

            if th_val > safe_limit:
                logger.warning(f"Baseline 次高温段温度 ({th_val}℃) exceeds allowed max ({allowed_max_temp}℃). Adjusting to {safe_limit}℃")
                baseline['工艺参数']['温度程序']['次高温段温度_摄氏'] = round(safe_limit, 1)

                # Ensure 最高温段保温温度 > 次高温段温度 - keep it at least 50 ℃ higher
                try:
                    tmax = baseline['工艺参数']['温度程序'].get('最高温段保温温度_摄氏')
                    th = baseline['工艺参数']['温度程序']['次高温段温度_摄氏']
                    if isinstance(tmax, (int, float)) and isinstance(th, (int, float)):
                        temp_diff = float(tmax) - th
                        if temp_diff < 50:
                            baseline['工艺参数']['温度程序']['最高温段保温温度_摄氏'] = round(th + 50.0, 1)
                            logger.warning(f"Baseline 最高温与次高温温差不足50℃. 调整最高温至 {th + 50.0}℃")
                except Exception:
                    pass
    except Exception:
        logger.debug("Unable to enforce temperature constraints on baseline (parsing error)")
    return baseline



def build_scheme_generation_prompt(
    target_material: Dict,
    param_windows: Dict,
    feature_summary: Dict,
    user_preferences: Dict,
    baseline_scheme: Dict
) -> str:
    """
    Build prompt for LLM to generate recipe schemes.

    Args:
        target_material: Dict containing target material info
        param_windows: Dict containing parameter windows
        feature_summary: Dict containing typical growth schemes
        user_preferences: Dict containing user preferences
        baseline_scheme: Dict containing baseline scheme

    Returns:
        Formatted prompt string
    """
    # Extract data
    windows = param_windows.get("参数窗口", {})
    lab_constraints = param_windows.get("实验室约束", {})
    typical_schemes = feature_summary.get("典型生长方案", [])

    # Build parameter windows string
    param_windows_str = ""
    for param_name, param_data in windows.items():
        param_type = param_data.get("参数类型", "")
        if param_type == "数值":
            default_val = param_data.get("推荐默认值", "N/A")
            min_val = param_data.get("窗口下界", "N/A")
            max_val = param_data.get("窗口上界", "N/A")
            param_windows_str += f"- {param_name}: 推荐默认值={default_val}, 窗口=[{min_val}, {max_val}]\n"
        elif param_type == "类别":
            candidates = param_data.get("候选取值列表", [])
            candidates_str = ", ".join([f"{c.get('取值', 'N/A')} (支持度={c.get('加权支持度', 'N/A')})" for c in candidates])
            param_windows_str += f"- {param_name}: 候选取值及支持度=({candidates_str})\n"

    # Build typical schemes string
    typical_schemes_str = ""
    for i, scheme in enumerate(typical_schemes, 1):
        scheme_id = scheme.get("方案ID", f"scheme_{i:03d}")
        scheme_name = scheme.get("方案名称", "N/A")
        core_features = scheme.get("核心特征", "N/A")
        applicable = scheme.get("适用", "N/A")
        recipe_info = scheme.get("代表配方ID列表", [])
        material_info = scheme.get("对应材料ID-化学式列表", [])
        typical_schemes_str += f"[{scheme_id}] {scheme_name}：{core_features}，适用于{applicable} （参考配方ID={recipe_info}，参考材料ID-化学式={material_info}）\n"

    # Build lab constraints string
    lab_constraints_str = f"""- 最高允许温度: {lab_constraints.get('最高允许温度_摄氏', 1000)}℃
- 降温速率范围: [{lab_constraints.get('最小降温速率_℃每小时', 0.5)}, {lab_constraints.get('最大降温速率_℃每小时', 300)}]℃/h
- 最长实验时长: {lab_constraints.get('最长单次实验时长_h', 200)}h"""

    # Build user preferences string
    user_prefs_str = f"""- 期望方案数量: {user_preferences.get('期望方案数量', 5)}
- 策略偏好: {user_preferences.get('策略偏好', '多样覆盖')}
- 优先大尺寸晶体: {user_preferences.get('优先大尺寸晶体', True)}
- 优先缩短实验时长: {user_preferences.get('优先缩短实验时长', False)}"""

    prompt = f"""目标材料：
- 化学式: {target_material.get('化学式', 'N/A')}
- 结构原型: {target_material.get('结构原型', 'N/A')}
- 材料族系: {target_material.get('材料族系', [])}

参数窗口（基于相似配方统计）：
{param_windows_str}

实验室约束：
{lab_constraints_str}

制备元素沸点温度约束：
- 最高温段保温温度可以超过制备元素中沸点最低的温度（用于熔化高沸点元素）
- 次高温段保温温度不得超过制备元素中沸点最低的温度（用于反应和晶体生长）
- 温度层次原则：最高温段 > 次高温段 > 低温段，相邻段温差建议≥50℃

典型生长方案参考（从相似配方中提炼）：
{typical_schemes_str}

用户偏好：
{user_prefs_str}

基线方案（使用推荐默认值）：
{json.dumps(baseline_scheme, ensure_ascii=False, indent=2)}

任务要求：
1. 生成 {user_preferences.get('期望方案数量', 5)} 套实验方案，包含基线方案和变体方案
2. 变体方案应参考"典型生长方案"，在关键参数上做有目的的调整
3. 每套方案必须包含完整的工艺参数、预期结果、溯源信息
4. 根据用户偏好调整方案设计：
   - 优先大尺寸晶体={user_preferences.get('优先大尺寸晶体', True)} → {'倾向慢冷、长保温方案' if user_preferences.get('优先大尺寸晶体', True) else '可以快冷、短保温'}
   - 策略偏好="{user_preferences.get('策略偏好', '多样覆盖')}" → {'确保方案类型多样化' if user_preferences.get('策略偏好') == '多样覆盖' else '根据偏好调整'}

输出格式：严格的JSON，结构如下：
{{
  "目标材料": {{
    "化学式": "{target_material.get('化学式', 'N/A')}",
    "结构原型": "{target_material.get('结构原型', 'N/A')}",
    "是否二维": {str(target_material.get('是否二维', False)).lower()},
    "是否半导体": {str(target_material.get('是否半导体', False)).lower()},
    "材料族系": {json.dumps(target_material.get('材料族系', []), ensure_ascii=False)}
  }},
  "推荐实验方案列表": [
    {{
      "方案ID": "方案_A",
      "方案类型": "baseline",
      "方案给人的一句话说明": "...",
      "工艺参数": {{
        "原料信息": "...",
        "原料标准化": "形如：Al:In:Se=1:1:3",
        "助熔剂信息": "...",
        "助熔剂标准化": "形如：Sn:AlInS3=10:1",
        "容器": "...",
        "籽晶": "...",
        "温度程序": {{
          "是否存在次高温预反应段": "...",
          "升温到次高温时间_h": 10.0,
          "次高温段温度_摄氏": 600.0,
          "次高温段保温时间_h": 12.0,
          "升温到最高温时间_h": 20.0,
          "最高温段保温温度_摄氏": 950.0,
          "最高温段保温时间_h": 24.0,
          "降温速率_主降温_℃每小时": 2.0,
          "降温时间_主降温_h": 100.0,
          "低温段保温温度_摄氏": 700.0,
          "低温段保温时间_h": 24.0,
          "冷却速率_至室温_标签": "炉冷"
        }},
        "分离与后处理": {{
          "分离方式": "...",
          "分离温度_摄氏": 600.0,
          "晶体的进一步处理": "..."
        }}
      }},
      "预期结果标签": {{
        "预期晶体尺寸": "...",
        "预期风险水平": "...",
        "风险来源简述": [...]
      }},
      "溯源信息": {{
        "主要参考配方ID": ["rec_001", "rec_006"],
        "参考材料ID-化学式": ["mat_001-Eu2AuGe3", "mat_003-MoS2"],
        "参考方案类型ID-名称": ["scheme_001-高助熔剂稀释方案", "scheme_002-极慢冷大尺寸单晶方案"]
      }}
    }},
    ...
  ],
  "整体备注": [
    "所有方案均满足实验室温度与降温速率约束，具体配比仍需根据目标化学式微调。",
    "建议先从方案 A + 方案 B 起步，根据首轮晶体尺寸和副相情况再收缩窗口。"
  ]
}}

关键要求：
- 所有方案必须满足实验室约束
- 所有方案温度参数必须考虑目标材料的热稳定性，最高温度可以超过元素沸点用于熔化，次高温温度不得超过制备元素中沸点最低的温度
- 所有方案里的助熔剂设计需优先考量安全性与稳定性，规避剧毒、强腐蚀性、高挥发性及化学性质活泼的试剂，优先选用安全稳定的助熔剂
- 每个方案的"方案类型"应对应一种典型生长方案（如：高助熔剂、极慢冷大尺寸、中温通用平衡等）
- 溯源信息必须包含三个字段：
  1. "主要参考配方ID": 引用具体的配方ID列表（例如：["rec_0078", "rec_0098"]）
  2. "参考材料ID-化学式": 引用材料ID和化学式的组合列表（例如：["mat_032-Eu2AuGe3", "mat_001-(Pb, La)(Zr, Sn, Ti)O3"]）
  3. "参考方案类型ID-名称": 引用典型生长方案的ID和名称组合列表（例如：["scheme_001-高助熔剂稀释方案", "scheme_002-极慢冷大尺寸单晶方案"]）
- 溯源信息的三个字段必须保持一致性，引用的配方、材料、方案类型应该相互对应
- 预期结果标签要基于参数特点给出合理预测

不要输出markdown代码块，直接输出JSON。"""

    return prompt


def generate_recipe_schemes(
    target_material: Dict,
    param_windows: Dict,
    feature_summary: Dict,
    user_preferences: Dict
) -> Dict:
    """
    Stage 2: Generate recipe schemes based on parameter windows and typical schemes.

    Args:
        target_material: Dict containing target material info
        param_windows: Dict containing parameter windows
        feature_summary: Dict containing typical growth schemes
        user_preferences: Dict containing user preferences

    Returns:
        Dict with 目标材料, 推荐实验方案列表, 整体备注
    """
    logger.info("Stage 2: Generating recipe schemes")

    # Build baseline scheme
    baseline_scheme = build_baseline_scheme(param_windows, target_material)

    # Build prompt
    prompt = build_scheme_generation_prompt(
        target_material,
        param_windows,
        feature_summary,
        user_preferences,
        baseline_scheme
    )

    # Call LLM with retry
    system_prompt = "你是材料晶体生长工艺设计专家，基于相似材料配方和参数窗口，为目标材料设计多套实验方案。"

    ## way1：让LLM推理实验方案
    ## way2：从cache中提取实验方案
    # print("debug==============\n", target_material[:50])
    material_id_formula = target_material.get("化学式", "unknown")

    if _USE_CACHE:
        logger.info(f"Using cache for recipe schemes: {material_id_formula}")
        cache_dir = Path(__file__).parent.parent / 'data' / 'recommand_recipe'
        cache_file = cache_dir / f'output_result-{material_id_formula}.jsonl'
        with open(cache_file, 'r', encoding='utf-8') as f:
            recipe_schemes = json.load(f)
    else:
        logger.info("Using LLM for recipe schemes")
        response = retry_llm_call(prompt, system_prompt)
        recipe_schemes = response2json(response)

    if not recipe_schemes:
        logger.error("Failed to parse recipe schemes JSON")
        sys.exit(1)

    # Validate structure
    if "推荐实验方案列表" not in recipe_schemes:
        logger.error("Recipe schemes missing required field: 推荐实验方案列表")
        sys.exit(1)

    logger.info(f"Recipe schemes generated: {len(recipe_schemes.get('推荐实验方案列表', []))} schemes")

    return recipe_schemes


# ============================================================================
# Main Pipeline
# ============================================================================

def main():
    """
    Main pipeline orchestrator.
    """
    parser = argparse.ArgumentParser(description="Multi-Scheme Recipe Generation System")
    parser.add_argument("--similar-file", required=True, help="Path to similar recipes file")
    parser.add_argument("--window-file", required=True, help="Path to parameter windows file")
    parser.add_argument("--input-file", required=True, help="Path to input template file")
    parser.add_argument("--output-file", required=True, help="Path to output file")
    parser.add_argument("--feature-output", required=True, help="Path to feature summary output file")
    parser.add_argument("--use-existed-feature", action='store_true', help="Use existing feature summary file instead of regenerating")

    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("Multi-Scheme Recipe Generation System")
    logger.info("=" * 80)

    # Load input files
    logger.info("Loading input files...")
    similar_recipes = load_jsonl(args.similar_file)
    param_windows = load_jsonl(args.window_file)
    input_template = load_jsonl(args.input_file)
    input_template["目标材料"] = similar_recipes.get("目标材料", {})  # Ensure target material is included in input template

    # Extract data from input template
    target_material = input_template.get("目标材料", {})
    user_preferences = input_template.get("用户方案设计偏好", {})
    print(f"Target material: {target_material.get('化学式', 'N/A')}")
    print(f"User preferences: {user_preferences}")
    # input("Press Enter to continue with recipe generation...")

    # Stage 1: Generate feature summary
    logger.info("-" * 80)
    if args.use_existed_feature:
        logger.info(f"Using existing feature summary from {args.feature_output}")
        feature_summary = load_jsonl(args.feature_output)
    else:
        feature_summary = generate_feature_summary(similar_recipes)

        # Write feature summary to file
        logger.info(f"Writing feature summary to {args.feature_output}")
        with open(args.feature_output, 'w', encoding='utf-8') as f:
            json.dump(feature_summary, f, ensure_ascii=False, indent=2)

    # Stage 2: Generate recipe schemes
    logger.info("-" * 80)
    # input("Press Enter to start recipe scheme generation...")
    recipe_schemes = generate_recipe_schemes(
        target_material,
        param_windows,
        feature_summary,
        user_preferences
    )

    # Write final output to file
    logger.info(f"Writing recipe schemes to {args.output_file}")
    with open(args.output_file, 'w', encoding='utf-8') as f:
        json.dump(recipe_schemes, f, ensure_ascii=False, indent=2)

    logger.info("=" * 80)
    logger.info("Pipeline completed successfully!")
    logger.info(f"Feature summary: {args.feature_output}")
    logger.info(f"Recipe schemes: {args.output_file}")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
