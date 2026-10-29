"""
Parameter Statistics and Window Determination

This module analyzes recipe parameters from similar materials and generates
recommended parameter windows with statistical backing.

Author: Claude Code
Date: 2026-02-07

Usage:
    python -m src.statistic_window \
        --similar-file data/similar_mates/similar_on-base_processed.jsonl \
        --input-file data/recommand_window/input_template.jsonl \
        --output-file data/recommand_window/output_result.jsonl
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any, Set
from collections import Counter

import numpy as np

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
        # Simple approach: remove everything after // if not in quotes
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
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        sys.exit(1)


def load_similar_materials(file_path: str) -> Dict:
    """
    Load similar materials file.

    Args:
        file_path: Path to similar_on-base_processed.jsonl

    Returns:
        Dictionary with target material and similar materials list
    """
    logger.info(f"Loading similar materials from {file_path}")
    data = load_jsonl(file_path)

    similar_count = len(data.get('相似材料列表', []))
    logger.info(f"Loaded {similar_count} similar materials")

    return data


def load_input_template(file_path: str) -> Dict:
    """
    Load input template with lab constraints and settings.

    Args:
        file_path: Path to input_template.jsonl

    Returns:
        Dictionary with lab constraints and window calculation settings
    """
    logger.info(f"Loading input template from {file_path}")
    data = load_jsonl(file_path)

    logger.info(f"Lab constraints: {data.get('实验室约束', {})}")
    logger.info(f"Window settings: {data.get('窗口计算设置', {})}")

    return data


# ============================================================================
# Numerical Parsing Functions (adapted from flux_retrieval.py)
# ============================================================================

def _parse_single_number(text: Any) -> Optional[float]:
    """
    Parse single number from text with regex.

    Args:
        text: Text or number to parse

    Returns:
        Parsed float value or None if parsing fails
    """
    if text is None or text == "":
        return None

    # Already a number
    if isinstance(text, (int, float)):
        return float(text)

    # Not a string
    if not isinstance(text, str):
        return None

    # Extract numbers from string
    nums = re.findall(r"[-+]?\d*\.?\d+", text.replace(",", " "))
    if not nums:
        return None

    values = [float(n) for n in nums]

    # Single number
    if len(values) == 1:
        return values[0]

    # Multiple numbers: return average (e.g., "1-10" → 5.5)
    return sum(values) / len(values)


def safe_get(data: Dict, *keys, default=None) -> Any:
    """
    Safely get nested dictionary value.

    Args:
        data: Dictionary to navigate
        *keys: Sequence of keys to traverse
        default: Default value if key not found

    Returns:
        Value at nested key path or default
    """
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key, {})
        else:
            return default
    return data if data != {} else default


# ============================================================================
# Parameter Discovery and Classification
# ============================================================================

def extract_all_recipes(similar_materials_data: Dict) -> List[Tuple[str, Dict]]:
    """
    Extract all recipes from similar materials list.

    Args:
        similar_materials_data: Data from similar materials file

    Returns:
        List of (recipe_id, recipe_dict) tuples
    """
    recipes = []

    for material in similar_materials_data.get('相似材料列表', []):
        for recipe in material.get('配方列表', []):
            recipe_id = recipe.get('配方ID', 'unknown')
            recipes.append((recipe_id, recipe))

    logger.info(f"Extracted {len(recipes)} recipes from similar materials")
    return recipes


def discover_all_parameters(recipes: List[Tuple[str, Dict]]) -> Set[str]:
    """
    Discover all parameter paths in recipes.

    Args:
        recipes: List of (recipe_id, recipe_dict) tuples

    Returns:
        Set of parameter paths (e.g., "工艺配方.助熔剂_对_溶质_摩尔比")
    """
    parameter_paths = set()

    sections = ["工艺配方", "温度程序", "分离与后处理"]

    for recipe_id, recipe in recipes:
        for section in sections:
            section_data = recipe.get(section, {})
            if isinstance(section_data, dict):
                for key in section_data.keys():
                    param_path = f"{section}.{key}"
                    parameter_paths.add(param_path)

    logger.info(f"Discovered {len(parameter_paths)} unique parameters")
    return parameter_paths


def extract_parameter_values(
    recipes: List[Tuple[str, Dict]],
    param_path: str
) -> List[Tuple[str, Any]]:
    """
    Extract values for a specific parameter across all recipes.

    Args:
        recipes: List of (recipe_id, recipe_dict) tuples
        param_path: Parameter path (e.g., "工艺配方.容器")

    Returns:
        List of (recipe_id, value) tuples (excludes None/empty values)
    """
    values = []

    # Parse parameter path
    parts = param_path.split('.', 1)
    if len(parts) != 2:
        return values

    section, key = parts

    for recipe_id, recipe in recipes:
        value = safe_get(recipe, section, key)

        # Skip None and empty strings
        if value is not None and value != "":
            values.append((recipe_id, value))

    return values


def classify_parameter_type(values: List[Tuple[str, Any]]) -> str:
    """
    Automatically determine if parameter is numerical or categorical.

    Args:
        values: List of (recipe_id, value) tuples

    Returns:
        "数值" for numerical, "类别" for categorical, "unknown" if no data
    """
    if not values:
        return "unknown"

    # Try to parse each value as a number
    numerical_count = 0

    for recipe_id, value in values:
        # Handle list types (e.g., 助熔剂家族标签)
        if isinstance(value, list):
            continue

        # Try parsing as number
        parsed = _parse_single_number(value)
        if parsed is not None:
            numerical_count += 1

    # If 80%+ are numerical, treat as numerical parameter
    if numerical_count / len(values) >= 0.8:
        return "数值"
    else:
        return "类别"


def build_parameter_catalog(recipes: List[Tuple[str, Dict]]) -> Dict[str, Dict]:
    """
    Build catalog of all parameters with their types and values.

    Args:
        recipes: List of (recipe_id, recipe_dict) tuples

    Returns:
        Dictionary mapping param_path to {"type": type, "values": [(recipe_id, value)]}
    """
    logger.info("Building parameter catalog...")

    # Discover all parameters
    param_paths = discover_all_parameters(recipes)

    catalog = {}

    for param_path in param_paths:
        # Extract values
        values = extract_parameter_values(recipes, param_path)

        if not values:
            continue

        # Classify type
        param_type = classify_parameter_type(values)

        if param_type == "unknown":
            continue

        catalog[param_path] = {
            "type": param_type,
            "values": values
        }

        logger.info(f"  {param_path}: {param_type} ({len(values)} samples)")

    logger.info(f"Catalog complete: {len(catalog)} parameters")
    return catalog


# ============================================================================
# Outlier Removal Functions
# ============================================================================

def remove_outliers_zscore(
    values: List[float],
    threshold: float
) -> List[float]:
    """
    Remove outliers using z-score method.

    Args:
        values: List of numerical values
        threshold: Z-score threshold (e.g., 2.5)

    Returns:
        Filtered list of values
    """
    if len(values) < 3:
        return values

    arr = np.array(values)
    mean = np.mean(arr)
    std = np.std(arr)

    if std == 0:
        return values

    z_scores = np.abs((arr - mean) / std)
    filtered = arr[z_scores <= threshold]

    removed_count = len(values) - len(filtered)
    if removed_count > 0:
        logger.debug(f"Removed {removed_count} outliers using z-score")

    return filtered.tolist()


def remove_outliers_percentile(
    values: List[float],
    percentile_range: List[int]
) -> Tuple[List[float], float, float]:
    """
    Remove outliers using percentile clipping.

    Args:
        values: List of numerical values
        percentile_range: [lower, upper] percentiles (e.g., [10, 90])

    Returns:
        Tuple of (filtered_values, p_lower, p_upper)
    """
    if len(values) < 3:
        return values, min(values), max(values)

    arr = np.array(values)

    # First remove extreme outliers (top/bottom 5%)
    p5, p95 = np.percentile(arr, [5, 95])
    clipped = arr[(arr >= p5) & (arr <= p95)]

    if len(clipped) == 0:
        clipped = arr

    # Then use specified percentile range as window
    p_lower, p_upper = np.percentile(clipped, percentile_range)

    removed_count = len(values) - len(clipped)
    if removed_count > 0:
        logger.debug(f"Removed {removed_count} extreme outliers using percentile")

    return clipped.tolist(), p_lower, p_upper


# ============================================================================
# Statistical Calculation Functions
# ============================================================================

def calculate_numerical_stats(
    values: List[Tuple[str, Any]],
    outlier_settings: Dict
) -> Dict:
    """
    Calculate statistics for numerical parameter.

    Args:
        values: List of (recipe_id, value) tuples
        outlier_settings: Outlier removal settings from input file

    Returns:
        Dictionary with statistical measures
    """
    # Parse numerical values
    parsed_values = []
    recipe_value_map = {}

    for recipe_id, value in values:
        parsed = _parse_single_number(value)
        if parsed is not None:
            parsed_values.append(parsed)
            recipe_value_map[recipe_id] = parsed

    if not parsed_values:
        return None

    # Remove outliers if enabled
    filtered_values = parsed_values

    if outlier_settings.get('启用', False):
        method = outlier_settings.get('方法', 'zscore')

        if method == 'zscore':
            threshold = outlier_settings.get('阈值', 2.5)
            filtered_values = remove_outliers_zscore(parsed_values, threshold)
        elif method == 'percentile':
            percentile_range = outlier_settings.get('分位数范围', [10, 90])
            filtered_values, _, _ = remove_outliers_percentile(parsed_values, percentile_range)

    if not filtered_values:
        logger.warning("All values removed by outlier filter, using original data")
        filtered_values = parsed_values

    # Calculate statistics
    arr = np.array(filtered_values)

    stats = {
        "样本数": len(filtered_values),
        "最小值": float(np.min(arr)),
        "最大值": float(np.max(arr)),
        "P10": float(np.percentile(arr, 10)),
        "P90": float(np.percentile(arr, 90)),
        "平均值": float(np.mean(arr)),
        "标准差": float(np.std(arr)),
        "中位数": float(np.median(arr)),
        "recipe_value_map": recipe_value_map
    }

    return stats


def calculate_categorical_stats(
    values: List[Tuple[str, Any]]
) -> Dict:
    """
    Calculate statistics for categorical parameter.

    Args:
        values: List of (recipe_id, value) tuples

    Returns:
        Dictionary with frequency counts
    """
    # Handle list-type values (e.g., 助熔剂家族标签)
    all_values = []
    recipe_value_map = {}

    for recipe_id, value in values:
        if isinstance(value, list):
            # Flatten list values
            for item in value:
                if item:
                    all_values.append(str(item))
            recipe_value_map[recipe_id] = value
        else:
            all_values.append(str(value))
            recipe_value_map[recipe_id] = str(value)

    if not all_values:
        return None

    # Count frequencies
    counter = Counter(all_values)

    # Build candidate list
    candidates = []
    for value, count in counter.most_common():
        candidates.append({
            "取值": value,
            "样本数": count,
            "加权支持度": float(count),  # Could weight by similarity score
            "代表性配方ID列表": []
        })

    stats = {
        "候选取值列表": candidates,
        "recipe_value_map": recipe_value_map
    }

    return stats


# ============================================================================
# Lab Constraint Application
# ============================================================================

def apply_lab_constraints(
    stat_window: Dict,
    lab_constraints: Dict,
    param_name: str
) -> Dict:
    """
    Apply lab constraints to statistical window.

    Args:
        stat_window: Statistical window with 下限 and 上限
        lab_constraints: Lab constraints from input file
        param_name: Parameter name to check for specific constraints

    Returns:
        Constrained window
    """
    lower = stat_window.get("下限", 0)
    upper = stat_window.get("上限", float('inf'))

    # Apply parameter-specific constraints
    if "温度" in param_name:
        max_temp = lab_constraints.get("最高允许温度_摄氏", float('inf'))
        upper = min(upper, max_temp)

    if "降温速率" in param_name:
        min_rate = lab_constraints.get("最小降温速率_℃每小时", 0)
        max_rate = lab_constraints.get("最大降温速率_℃每小时", float('inf'))
        lower = max(lower, min_rate)
        upper = min(upper, max_rate)

    if "时间" in param_name or "时长" in param_name:
        max_time = lab_constraints.get("最长单次实验时长_h", float('inf'))
        upper = min(upper, max_time)

    # Ensure lower <= upper
    if lower > upper:
        logger.warning(f"Constraint conflict for {param_name}: lower ({lower}) > upper ({upper})")
        lower = upper

    return {
        "下限": round(lower, 2),
        "上限": round(upper, 2)
    }


# ============================================================================
# Representative Recipe Selection
# ============================================================================

def find_representative_recipes(
    recipe_value_map: Dict[str, float],
    default_value: float,
    window_range: float,
    max_count: int = 3
) -> List[str]:
    """
    Find recipes with parameter values close to the default.

    Args:
        recipe_value_map: Mapping of recipe_id to value
        default_value: Recommended default value
        window_range: Range of the window (P90 - P10)
        max_count: Maximum number of recipes to return

    Returns:
        List of representative recipe IDs
    """
    tolerance = 0.1 * window_range if window_range > 0 else 0.1 * abs(default_value)

    candidates = []
    for recipe_id, value in recipe_value_map.items():
        if abs(value - default_value) <= tolerance:
            candidates.append((recipe_id, abs(value - default_value)))

    # Sort by distance to default
    candidates.sort(key=lambda x: x[1])

    return [recipe_id for recipe_id, _ in candidates[:max_count]]


def find_representative_recipes_categorical(
    recipe_value_map: Dict[str, Any],
    default_value: str
) -> List[str]:
    """
    Find recipes with the default categorical value.

    Args:
        recipe_value_map: Mapping of recipe_id to value
        default_value: Recommended default value

    Returns:
        List of representative recipe IDs
    """
    candidates = []

    for recipe_id, value in recipe_value_map.items():
        # Handle list values
        if isinstance(value, list):
            if default_value in value:
                candidates.append(recipe_id)
        else:
            if str(value) == default_value:
                candidates.append(recipe_id)

    return candidates[:3]


# ============================================================================
# Output Formatting
# ============================================================================

def format_numerical_parameter(
    param_name: str,
    stats: Dict,
    lab_constraints: Dict,
    min_samples: int
) -> Optional[Dict]:
    """
    Format numerical parameter for output.

    Args:
        param_name: Parameter name
        stats: Statistical measures
        lab_constraints: Lab constraints
        min_samples: Minimum sample count

    Returns:
        Formatted parameter dictionary or None if insufficient data
    """
    if stats is None:
        return None

    sample_count = stats.get("样本数", 0)
    if sample_count < min_samples:
        logger.warning(f"Insufficient samples for {param_name}: {sample_count} < {min_samples}")
        return None

    # Determine unit from parameter name
    unit = ""
    if "摩尔比" in param_name:
        unit = "mol_ratio"
    elif "温度" in param_name and "摄氏" in param_name:
        unit = "摄氏度"
    elif "时间" in param_name and "_h" in param_name:
        unit = "小时"
    elif "速率" in param_name and "℃每小时" in param_name:
        unit = "℃每小时"

    # Statistical window
    stat_window = {
        "下限": stats["P10"],
        "上限": stats["P90"]
    }

    # Apply lab constraints
    constrained_window = apply_lab_constraints(stat_window, lab_constraints, param_name)

    # Default value (use median)
    default_value = round(stats["中位数"], 2)

    # Find representative recipes
    window_range = stats["P90"] - stats["P10"]
    recipe_value_map = stats.get("recipe_value_map", {})
    representative_recipes = find_representative_recipes(
        recipe_value_map,
        default_value,
        window_range
    )

    return {
        "参数类型": "数值",
        "单位": unit,
        "文献统计": {
            "样本数": sample_count,
            "最小值": round(stats["最小值"], 2),
            "最大值": round(stats["最大值"], 2),
            "P10": round(stats["P10"], 2),
            "P90": round(stats["P90"], 2),
            "平均值": round(stats["平均值"], 2),
            "标准差": round(stats["标准差"], 2)
        },
        "实验室约束裁剪后窗口": constrained_window,
        "推荐默认值": default_value,
        "代表性配方ID列表": representative_recipes
    }


def format_categorical_parameter(
    param_name: str,
    stats: Dict,
    min_samples: int
) -> Optional[Dict]:
    """
    Format categorical parameter for output.

    Args:
        param_name: Parameter name
        stats: Statistical measures
        min_samples: Minimum sample count

    Returns:
        Formatted parameter dictionary or None if insufficient data
    """
    if stats is None:
        return None

    candidates = stats.get("候选取值列表", [])
    if not candidates:
        return None

    total_samples = sum(c["样本数"] for c in candidates)
    if total_samples < min_samples:
        logger.warning(f"Insufficient samples for {param_name}: {total_samples} < {min_samples}")
        return None

    # Find representative recipes for each candidate
    recipe_value_map = stats.get("recipe_value_map", {})

    for candidate in candidates:
        value = candidate["取值"]
        representative_recipes = find_representative_recipes_categorical(
            recipe_value_map,
            value
        )
        candidate["代表性配方ID列表"] = representative_recipes

    # Default value (most frequent)
    default_value = candidates[0]["取值"] if candidates else ""

    return {
        "参数类型": "类别",
        "候选取值列表": candidates,
        "推荐默认取值": default_value
    }


def build_output_structure(
    target_material: Dict,
    lab_constraints: Dict,
    sample_info: Dict,
    param_windows: Dict
) -> Dict:
    """
    Build final output structure.

    Args:
        target_material: Target material info
        lab_constraints: Lab constraints
        sample_info: Sample count information
        param_windows: Parameter windows dictionary

    Returns:
        Complete output dictionary
    """
    return {
        "目标材料": target_material,
        "实验室约束": lab_constraints,
        "参与统计的样本数": sample_info,
        "参数窗口": param_windows
    }


# ============================================================================
# Main Pipeline
# ============================================================================

def calculate_parameter_windows(
    similar_file_path: str,
    input_file_path: str,
    output_file_path: str
) -> None:
    """
    Calculate statistical parameter windows from similar materials.

    Args:
        similar_file_path: Path to similar_on-base_processed.jsonl
        input_file_path: Path to input_template.jsonl
        output_file_path: Path to write output_template.jsonl
    """
    logger.info("=" * 80)
    logger.info("Starting parameter window calculation")
    logger.info("=" * 80)

    # Step 1: Load data
    similar_data = load_similar_materials(similar_file_path)
    input_data = load_input_template(input_file_path)

    # Step 2: Extract recipes
    recipes = extract_all_recipes(similar_data)

    if not recipes:
        logger.error("No recipes found in similar materials")
        sys.exit(1)

    # Step 3: Build parameter catalog
    catalog = build_parameter_catalog(recipes)

    if not catalog:
        logger.error("No parameters discovered")
        sys.exit(1)

    # Step 4: Get settings
    lab_constraints = input_data.get('实验室约束', {})
    window_settings = input_data.get('窗口计算设置', {})
    min_samples = window_settings.get('最小有效样本数', 5)
    outlier_settings = window_settings.get('异常值过滤', {})

    # Step 5: Calculate statistics for each parameter
    logger.info("Calculating statistics for each parameter...")
    param_windows = {}

    for param_path, param_info in catalog.items():
        param_type = param_info["type"]
        values = param_info["values"]

        # Extract parameter name (remove section prefix)
        param_name = param_path.split('.', 1)[1]

        logger.info(f"Processing {param_name} ({param_type})...")

        if param_type == "数值":
            stats = calculate_numerical_stats(values, outlier_settings)
            formatted = format_numerical_parameter(
                param_name,
                stats,
                lab_constraints,
                min_samples
            )
        else:  # 类别
            stats = calculate_categorical_stats(values)
            formatted = format_categorical_parameter(
                param_name,
                stats,
                min_samples
            )

        if formatted:
            param_windows[param_name] = formatted

    logger.info(f"Generated windows for {len(param_windows)} parameters")

    # Step 6: Build output structure
    target_material = similar_data.get('目标材料', {})

    sample_info = {
        "总配方条数": len(recipes),
        "有尺寸信息的条数": sum(
            1 for _, recipe in recipes
            if recipe.get('晶体信息', {}).get('最大尺寸_mm')
        )
    }

    output = build_output_structure(
        target_material,
        lab_constraints,
        sample_info,
        param_windows
    )

    # Step 7: Write output
    logger.info(f"Writing output to {output_file_path}")

    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info("=" * 80)
    logger.info("Parameter window calculation complete!")
    logger.info("=" * 80)


# ============================================================================
# CLI Interface
# ============================================================================

def main():
    """CLI interface."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Parameter Statistics and Window Determination'
    )
    parser.add_argument(
        '--similar-file',
        type=str,
        required=True,
        help='Path to similar_on-base_processed.jsonl'
    )
    parser.add_argument(
        '--input-file',
        type=str,
        required=True,
        help='Path to input_template.jsonl'
    )
    parser.add_argument(
        '--output-file',
        type=str,
        required=True,
        help='Path to write output file'
    )

    args = parser.parse_args()

    calculate_parameter_windows(
        args.similar_file,
        args.input_file,
        args.output_file
    )


if __name__ == '__main__':
    main()
