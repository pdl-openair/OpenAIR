#!/usr/bin/env python3
"""
Post-process knowledge base data from recipe-centric to material-centric format.

This script groups recipes by material identity (4-tuple: 化学式, 结构原型, 是否二维, 是否半导体)
and expands chemical formulas with variables (e.g., "REMn2Si2 (RE=Y, Er)" -> ["YMn2Si2", "ErMn2Si2"]).
"""

import json
import re
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Any
from collections import defaultdict

# ============================================================================
# Logging Configuration
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# I/O Functions
# ============================================================================

def load_jsonl(file_path: str) -> List[Dict]:
    """Load records from JSONL file."""
    records = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON at line {line_num}: {e}")
        logger.info(f"Loaded {len(records)} records from {file_path}")
        return records
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return []
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return []


def write_jsonl(file_path: str, records: List[Dict]) -> None:
    """Write records to JSONL file."""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        logger.info(f"Wrote {len(records)} records to {file_path}")
    except Exception as e:
        logger.error(f"Error writing to {file_path}: {e}")


def safe_get(data: Dict, *keys, default="") -> Any:
    """Safely get nested dictionary value."""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key, {})
        else:
            return default
    return data if data else default

# ============================================================================
# Formula Expansion
# ============================================================================

def expand_chemical_formula(formula: str) -> List[str]:
    """
    Expand formulas with variables like 'REMn2Si2 (RE=Y, Er)' into ['YMn2Si2', 'ErMn2Si2'].

    Args:
        formula: Chemical formula string, possibly with variable substitutions

    Returns:
        List of expanded formulas. Returns [formula] if no variables found.

    Examples:
        >>> expand_chemical_formula("REMn2Si2 (RE=Y, Er)")
        ['YMn2Si2', 'ErMn2Si2']
        >>> expand_chemical_formula("GaN")
        ['GaN']
    """
    # Pattern: base_formula (VAR=elem1, elem2, ...)
    # Match patterns like (RE=Y, Er), (M=A, B, C), etc.
    pattern = r'(\w+)\s*\((\w+)=([^)]+)\)'
    match = re.search(pattern, formula)

    if not match:
        # No variables found, return as-is
        return [formula]

    base_formula = match.group(1)  # e.g., "REMn2Si2"
    variable = match.group(2)       # e.g., "RE"
    elements_str = match.group(3)  # e.g., "Y, Er"

    # Split elements by comma and strip whitespace
    elements = [elem.strip() for elem in elements_str.split(',')]

    # Replace variable in base formula with each element
    expanded = []
    for elem in elements:
        # Replace the variable (e.g., "RE") with the element (e.g., "Y")
        expanded_formula = base_formula.replace(variable, elem)
        expanded.append(expanded_formula)
        logger.debug(f"Expanded {formula} -> {expanded_formula}")

    logger.info(f"Expanded formula '{formula}' into {len(expanded)} variants: {expanded}")
    return expanded

# ============================================================================
# Material Identity
# ============================================================================

def get_material_key(material_info: Dict) -> Tuple[str, str, Any, Any]:
    """
    Extract 4-tuple identity key from 目标产物信息.

    Args:
        material_info: Dictionary containing material properties

    Returns:
        Tuple of (化学式, 结构原型, 是否二维, 是否半导体)
    """
    return (
        safe_get(material_info, "化学式", default=""),
        safe_get(material_info, "结构原型", default=""),
        material_info.get("是否二维"),
        material_info.get("是否半导体")
    )


def create_material_record(material_key: Tuple, recipes: List[Dict], material_id: str) -> Dict:
    """
    Create a material record with grouped recipes.

    Args:
        material_key: 4-tuple material identity
        recipes: List of recipe dictionaries
        material_id: Unique material identifier (e.g., "mat_001")

    Returns:
        Material record dictionary
    """
    化学式, 结构原型, 是否二维, 是否半导体 = material_key

    # Transform recipes to remove redundant material info
    配方列表 = []
    for recipe in recipes:
        配方 = {
            "配方ID": recipe.get("配方ID", ""),
            "来源文献ID": recipe.get("文献ID", ""),
            "实验目的": recipe.get("实验目的", ""),
            "工艺配方": recipe.get("工艺配方", {}),
            "温度程序": recipe.get("温度程序", {}),
            "分离与后处理": recipe.get("分离与后处理", {}),
            "晶体信息": recipe.get("晶体信息", {})
        }
        配方列表.append(配方)

    return {
        "材料ID": material_id,
        "化学式": 化学式,
        "结构原型": 结构原型,
        "是否二维": 是否二维,
        "是否半导体": 是否半导体,
        "配方列表": 配方列表
    }

# ============================================================================
# Main Processing
# ============================================================================

def process_knowledge_base(input_path: str, output_path: str) -> None:
    """
    Transform recipe-centric knowledge base to material-centric format.

    Process:
    1. Load input JSONL
    2. For each recipe:
       - Extract material info
       - Expand chemical formula if needed
       - Group recipes by material key (4-tuple)
    3. Generate material IDs
    4. Restructure data with material-level and recipe-level fields
    5. Write output JSONL

    Args:
        input_path: Path to input JSONL file (recipe-centric)
        output_path: Path to output JSONL file (material-centric)
    """
    logger.info(f"Starting knowledge base post-processing")
    logger.info(f"Input: {input_path}")
    logger.info(f"Output: {output_path}")

    # Load input data
    recipes = load_jsonl(input_path)
    if not recipes:
        logger.error("No recipes loaded, aborting")
        return

    # Group recipes by material identity
    # Key: (化学式, 结构原型, 是否二维, 是否半导体)
    # Value: List of recipe dictionaries
    material_groups = defaultdict(list)

    for recipe in recipes:
        recipe_id = recipe.get("配方ID", "unknown")
        material_info = recipe.get("目标产物信息", {})

        if not material_info:
            logger.warning(f"Recipe {recipe_id} missing 目标产物信息, skipping")
            continue

        # Get original chemical formula
        original_formula = safe_get(material_info, "化学式", default="")
        if not original_formula:
            logger.warning(f"Recipe {recipe_id} missing 化学式, skipping")
            continue

        # Expand formula if it contains variables
        expanded_formulas = expand_chemical_formula(original_formula)

        # For each expanded formula, create a material key and group
        for expanded_formula in expanded_formulas:
            # Create modified material info with expanded formula
            modified_material_info = material_info.copy()
            modified_material_info["化学式"] = expanded_formula

            # Get material key (4-tuple)
            material_key = get_material_key(modified_material_info)

            # Create a copy of recipe with modified material info
            recipe_copy = recipe.copy()
            recipe_copy["目标产物信息"] = modified_material_info

            # Add to group
            # - 后处理改进：如果配方来源文献ID一致，不做添加
            existing_recipes = material_groups[material_key]
            is_duplicate = False
            for existing_recipe in existing_recipes:
                if existing_recipe.get("文献ID") == recipe_copy.get("文献ID"):
                    is_duplicate = True
                    break

            if not is_duplicate:
                material_groups[material_key].append(recipe_copy)
                logger.info(f"Grouped recipe {recipe_id} under material key: {material_key}")
            else:
                logger.info(f"Skipping duplicate recipe {recipe_id} for material key: {material_key}")

    logger.info(f"Grouped {len(recipes)} recipes into {len(material_groups)} materials")

    # Generate material records with sequential IDs
    material_records = []
    # 报错行前添加，打印所有键和对应类型
    # print("material_groups的键：", list(material_groups.keys()))
    # print("键的类型：", [(k, type(k)) for k in material_groups.keys()])
    for idx, (material_key, recipes_list) in enumerate(material_groups.items(), start=1):
        material_id = f"mat_{idx:03d}"
        material_record = create_material_record(material_key, recipes_list, material_id)
        material_records.append(material_record)

        化学式 = material_key[0]
        recipe_count = len(recipes_list)
        logger.info(f"Material {material_id}: {化学式} with {recipe_count} recipe(s)")

    # Write output
    write_jsonl(output_path, material_records)
    logger.info(f"Post-processing complete: {len(material_records)} materials written")

    # Summary statistics
    total_recipes = sum(len(m["配方列表"]) for m in material_records)
    logger.info(f"Summary: {len(material_records)} materials, {total_recipes} total recipes")

# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    """Command-line interface."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Post-process knowledge base from recipe-centric to material-centric format"
    )
    parser.add_argument(
        "--input",
        default="data/knowledge_base/except_test_linejson_small.jsonl",
        help="Input JSONL file path (recipe-centric)"
    )
    parser.add_argument(
        "--output",
        default="data/knowledge_base/except_test_linejson_small_processed.jsonl",
        help="Output JSONL file path (material-centric)"
    )

    args = parser.parse_args()

    # Resolve paths relative to project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    input_path = project_root / args.input
    output_path = project_root / args.output

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Process
    process_knowledge_base(str(input_path), str(output_path))


if __name__ == "__main__":
    main()
