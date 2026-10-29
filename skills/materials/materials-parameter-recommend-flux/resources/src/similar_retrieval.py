"""
Similar Material Retrieval System

This module implements a material similarity retrieval system that finds similar materials
based on structural and property features, then returns matching experimental recipes.

Author: Claude Code
Date: 2026-02-04

usage: python -m src.similar_retrieval \
    --query "用助熔剂法制备AlInSe₃" --top-k 30 \
    --kb-path data/knowledge_base/knowledge_base_processed.jsonl \
    --intuition-template data/similar_mates/intuition_template.jsonl \
    --requirement-template data/similar_mates/how_to_parse_intuition.jsonl \
    --output data/similar_mates/similar_on-base_processed-AlInSe3.jsonl
"""

import json
import logging
import re
from typing import Dict, List, Tuple, Optional
from pathlib import Path
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# Data Loading
# ============================================================================

def load_intuition_template(template_path: str) -> Dict:
    """Load intuition template from JSONL file, stripping JSON comments."""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        content = re.sub(r'//.*?$', '', content, flags=re.MULTILINE)
        return json.loads(content)
    except FileNotFoundError:
        logger.error(f"Template file not found: {template_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in template file: {e}")
        sys.exit(1)

def load_knowledge_base(kb_path: str) -> List[Dict]:
    """
    Load materials from JSONL knowledge base file.

    Args:
        kb_path: Path to knowledge base JSONL file (one material per line)

    Returns:
        List of material dictionaries
    """
    materials = []

    try:
        with open(kb_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    material = json.loads(line)
                    materials.append(material)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON at line {line_num}: {e}")
                    sys.exit(1)

        logger.info(f"Loaded {len(materials)} materials from knowledge base")
        return materials

    except FileNotFoundError:
        logger.error(f"Knowledge base file not found: {kb_path}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error loading knowledge base: {e}")
        sys.exit(1)


# ============================================================================
# Scoring Functions
# ============================================================================

def infer_material_family(
    chemical_formula: Optional[str],
    structure_prototype: Optional[str] = None,
    is_2d: Optional[bool] = None,
    is_semiconductor: Optional[bool] = None
) -> List[str]:
    """
    Infer material family tags based on chemical formula and other properties.

    This function uses rule-based matching to determine material family tags,
    ensuring consistency between target and candidate materials.

    Args:
        chemical_formula: Chemical formula of the material (e.g., "MoS2", "CsPbBr3")
        structure_prototype: Structure prototype (e.g., "Perovskite-type", "Wurtzite")
        is_2d: Whether the material is 2D
        is_semiconductor: Whether the material is a semiconductor

    Returns:
        Material family tags list: [主族系] or [主族系, 亚族系]
        Returns empty list [] if cannot determine
    """
    if not chemical_formula:
        return []

    formula = chemical_formula.strip()

    # Element pattern: matches element symbols (capital followed by optional lowercase)
    # and numbers (stoichiometry)
    element_pattern = r'([A-Z][a-z]?)(\d*\.?\d*)?'
    elements = re.findall(element_pattern, formula)

    # Clean up: filter out empty matches and parentheses content
    cleaned_elements = []
    for elem, stoichiometry in elements:
        if elem and elem not in ['(', ')', '[', ']', '{', '}']:
            cleaned_elements.append(elem)

    # Remove duplicates while preserving order
    unique_elements = []
    for e in cleaned_elements:
        if e not in unique_elements:
            unique_elements.append(e)

    # Build a set for quick lookup
    element_set = set(unique_elements)

    # =========================================================================
    # Main Family Inference (based on chemical formula)
    # =========================================================================

    main_family = None

    # ------------------------------------------
    # Chalcogenides (硫族化合物) - MXx, A2MX4
    # ------------------------------------------
    chalcogens = {'S', 'Se', 'Te'}
    transition_metals = {'Ti', 'Zr', 'Hf', 'V', 'Nb', 'Ta', 'Mo', 'W', 'Mn', 'Tc', 'Re', 'Fe', 'Co', 'Ni', 'Cu', 'Zn'}

    if element_set & chalcogens:
        if element_set & transition_metals:
            if {'Mo', 'W'} & element_set:
                main_family = "过渡金属硫族化合物(TMD)"
                if is_2d is True:
                    return [main_family, "层状过渡金属二硫族化合物"]
            elif {'Nb', 'Ta'} & element_set:
                main_family = "过渡金属硫族化合物(TMD)"
            else:
                main_family = "过渡金属硫族化合物"
        elif {'Ga', 'In', 'Al'} & element_set and len(element_set & chalcogens) >= 2:
            main_family = "铜基硫族化合物"
        elif {'Li', 'Na', 'K', 'Rb', 'Cs'} & element_set and len(unique_elements) <= 3:
            main_family = "碱金属硫族化合物"
        else:
            main_family = "硫族化合物"

    # ------------------------------------------
    # Borates (硼酸盐) - must check before oxides
    # ------------------------------------------
    elif 'B' in element_set and 'O' in element_set:
        main_family = "硼酸盐"

    # ------------------------------------------
    # Oxides (氧化物)
    # ------------------------------------------
    elif 'O' in element_set:
        # Check for common oxide structures first
        if structure_prototype:
            proto_lower = structure_prototype.lower()
            if 'spinel' in proto_lower:
                main_family = "尖晶石氧化物(Spinel)"
            elif 'rutile' in proto_lower or 'anatase' in proto_lower:
                main_family = "金红石型氧化物"
            elif 'pyrochlore' in proto_lower:
                main_family = "烧绿石氧化物"
            elif 'garnet' in proto_lower:
                main_family = "石榴石氧化物"
            elif 'wurtzite' in proto_lower or 'zincblende' in proto_lower:
                main_family = "纤锌矿型氧化物"
            elif 'perovskite' in proto_lower:
                main_family = "钙钛矿氧化物"
                if is_semiconductor is False and 'ferroelectric' in proto_lower:
                    return [main_family, "钙钛矿型铁电氧化物"]

        if not main_family:
            if {'Ca', 'Sr', 'Ba', 'La'} & element_set and 'O' in element_set:
                main_family = "氧化物"
            elif {'La', 'Ce', 'Pr', 'Nd', 'Sm', 'Eu', 'Gd', 'Tb', 'Dy', 'Ho', 'Er', 'Tm', 'Yb', 'Lu', 'Y'} & element_set:
                main_family = "稀土氧化物"
            else:
                main_family = "氧化物"

    # ------------------------------------------
    # III-V Semiconductors (III-V族半导体) - GaN, InP, etc.
    # ------------------------------------------
    elif {'Ga', 'In', 'Al'} & element_set and {'N', 'P', 'As', 'Sb'} & element_set:
        main_family = "III-V族半导体"

    # ------------------------------------------
    # Halides (卤化物) - ABX3, MX2
    # ------------------------------------------
    elif element_set & {'F', 'Cl', 'Br', 'I'}:
        halogens = element_set & {'F', 'Cl', 'Br', 'I'}
        # Check for perovskite structure
        if structure_prototype and 'perovskite' in structure_prototype.lower():
            main_family = "卤化物钙钛矿"
            # Double perovskite: Cs2AgBiBr6
            if len(unique_elements) >= 4 and {'Ag', 'Bi'} & element_set:
                return [main_family, "双钙钛矿结构"]
        elif {'Pb', 'Sn'} & element_set and {'Cs', 'MA', 'FA'} & element_set:
            main_family = "卤化物钙钛矿"
        elif {'Ca', 'Sr', 'Ba', 'Mg'} & element_set and halogens:
            main_family = "碱土金属卤化物"
        elif {'La', 'Ce', 'Y'} & element_set and halogens:
            main_family = "稀土卤化物"
        else:
            main_family = "卤化物"

    # ------------------------------------------
    # Silicides/Germanides (硅化物/锗化物)
    # ------------------------------------------
    elif 'Si' in element_set:
        if {'Y', 'La', 'Ce', 'Er', 'Sm', 'Gd'} & element_set and 'Si' in element_set:
            main_family = "稀土金属硅化物"
            if structure_prototype and 'thcr2si2' in structure_prototype.lower().replace('-', '').replace('_', ''):
                return [main_family, "ThCr2Si2型稀土硅化物"]
        elif {'Ti', 'Mo', 'W', 'Nb', 'Ta'} & element_set:
            main_family = "过渡金属硅化物"
        else:
            main_family = "金属硅化物"

    elif 'Ge' in element_set:
        main_family = "金属锗化物"

    # ------------------------------------------
    # Nitrides/Carbonitrides (氮化物/碳氮化物)
    # ------------------------------------------
    elif 'N' in element_set:
        if 'C' in element_set and 'N' in element_set and len(element_set) <= 2:
            main_family = "石墨相碳氮化物(g-C3N4)"
        elif {'Ti', 'Al', 'V', 'Nb', 'Ta'} & element_set:
            main_family = "金属氮化物"
            if 'C' in element_set:
                return [main_family, "三元金属碳氮化物"]
        else:
            main_family = "金属氮化物"

    # ------------------------------------------
    # Phosphates/Vanadates (磷酸盐/钒酸盐)
    # ------------------------------------------
    elif 'P' in element_set and 'O' in element_set:
        main_family = "磷酸盐"
    elif 'V' in element_set and 'O' in element_set:
        main_family = "钒酸盐"

    # ------------------------------------------
    # Phosphides/Arsenides/Antimonides (磷/砷/锑化物)
    # ------------------------------------------
    elif {'P', 'As', 'Sb'} & element_set:
        # Check for Kagome metals like CsV3Sb5
        if {'Cs', 'V'} & element_set and {'Sb', 'As'} & element_set:
            main_family = "拓扑Kagome金属"
        elif {'La', 'Ce', 'Y'} & element_set:
            main_family = "稀土磷/砷/锑化物"
        elif {'Ga', 'In'} & element_set:
            main_family = "III-V族半导体"
        else:
            main_family = "金属磷/砷/锑化物"

    # ------------------------------------------
    # Intermetallics (金属间化合物)
    # ------------------------------------------
    else:
        # Check for Laves phase: AB2
        if len(unique_elements) == 2:
            main_family = "金属间化合物"

    # =========================================================================
    # Sub-family refinement based on structure prototype
    # =========================================================================

    if main_family and structure_prototype:
        proto_lower = structure_prototype.lower()

        # Heusler phases
        if 'heusler' in proto_lower or 'inverse heusler' in proto_lower:
            if main_family == "金属间化合物":
                main_family = "Heusler相金属间化合物"
                if is_semiconductor is False:
                    return [main_family, "铁磁Heusler相"]

        # Laves phase
        if 'laves' in proto_lower:
            if main_family == "金属间化合物":
                return [main_family, "Laves相金属间化合物"]

        # ThCr2Si2 type
        if 'thcr2si2' in proto_lower.replace('-', '').replace('_', ''):
            if main_family != "ThCr2Si2型稀土硅化物":
                main_family = "ThCr2Si2型结构"

    # =========================================================================
    # 2D material refinement
    # =========================================================================

    if main_family and is_2d is True:
        if main_family == "过渡金属硫族化合物(TMD)" and "层状" not in str(main_family):
            return [main_family, "层状过渡金属二硫族化合物"]

    if not main_family:
        return []

    return [main_family]


def structure_prototype_score(proto1: Optional[str], proto2: Optional[str]) -> float:
    """
    Calculate structure prototype similarity score.

    Args:
        proto1: Structure prototype of target material
        proto2: Structure prototype of candidate material

    Returns:
        Score between 0.0 and 1.0:
        - 1.0: Exact match (case-insensitive)
        - 0.5: Partial match (substring or common keywords)
        - 0.0: No match or null values
    """
    # print(proto1, proto2)
    if not proto1 or not proto2:
        return 0.0

    proto1_lower = proto1.lower().strip()
    proto2_lower = proto2.lower().replace(" structure", "").strip()
    # print(proto1_lower, proto2_lower)

    # Exact match
    if proto1_lower == proto2_lower:
        return 1.0

    # Remove "-type" suffix for comparison
    base1 = re.sub(r'[-_\s]type$', '', proto1_lower)
    base2 = re.sub(r'[-_\s]type$', '', proto2_lower)
    # print(base1, base2)

    if base1 == base2:
        print(base1, base2)
        return 1.0

    # Partial match: check if one contains the other
    if base1 in proto2_lower or base2 in proto1_lower:
        return 0.5

    # Check for common structure keywords
    common_types = [
        'perovskite', 'spinel', 'wurtzite', 'rocksalt', 'fluorite',
        'pyrochlore', 'garnet', 'olivine', 'rutile', 'anatase',
        'delafossite', 'chalcopyrite', 'zincblende', 'diamond'
    ]

    for ctype in common_types:
        if ctype in proto1_lower and ctype in proto2_lower:
            return 0.5

    return 0.0


def boolean_match_score(val1: Optional[bool], val2: Optional[bool]) -> float:
    """
    Calculate boolean field similarity score.

    Args:
        val1: Boolean value of target material
        val2: Boolean value of candidate material

    Returns:
        Score between 0.0 and 1.0:
        - 1.0: Both same (true/true or false/false)
        - 0.5: One or both are null
        - 0.0: Different (true/false)
    """
    # Both null or both missing
    if val1 is None and val2 is None:
        return 0.5

    # One is null
    if val1 is None or val2 is None:
        return 0.5

    # Both same
    if val1 == val2:
        return 1.0

    # Different
    return 0.0


def family_match_score(family1: List[str], family2: List[str]) -> float:
    """
    Calculate material family similarity score.

    Args:
        family1: Material family tags of target material
        family2: Material family tags of candidate material

    Returns:
        Score between 0.0 and 1.0:
        - 1.0: Main family matches (first element)
        - 0.5: Sub-family matches (second element) or any element matches
        - 0.0: No match or empty lists
    """
    if not family1 or not family2:
        return 0.0

    # Main family match (first element)
    if family1[0] == family2[0]:
        return 1.0

    # Sub-family match (second element, if exists)
    if len(family1) > 1 and len(family2) > 1:
        if family1[1] == family2[1]:
            return 0.5

    # Check if any element matches
    if set(family1) & set(family2):
        return 0.5

    return 0.0


def calculate_material_similarity(
    target: Dict,
    candidate: Dict,
    weights: Dict
) -> float:
    """
    Calculate weighted similarity score between target and candidate materials.

    Args:
        target: Target material dictionary
        candidate: Candidate material dictionary
        weights: Weight dictionary with keys:
            - 结构原型_权重
            - 是否二维_权重
            - 是否半导体_权重
            - 材料族系_权重

    Returns:
        Weighted similarity score between 0.0 to 1.0
    """
    # Calculate individual component scores
    structure_score = structure_prototype_score(
        target.get('结构原型'),
        candidate.get('结构原型')
    )

    is_2d_score = boolean_match_score(
        target.get('是否二维'),
        candidate.get('是否二维')
    )

    is_semiconductor_score = boolean_match_score(
        target.get('是否半导体'),
        candidate.get('是否半导体')
    )

    # Infer material family using rule engine (for both target and candidate)
    # Priority: use existing field if available, otherwise infer from chemical formula
    target_family = target.get('材料族系')
    if not target_family:
        target_family = infer_material_family(
            target.get('化学式'),
            target.get('结构原型'),
            target.get('是否二维'),
            target.get('是否半导体')
        )
    print("~~~~~~~~~~~~~~~~~~~~~~~~")
    print(target.get('化学式'), "-->", target_family)

    candidate_family = candidate.get('材料族系')
    if not candidate_family:
        candidate_family = infer_material_family(
            candidate.get('化学式'),
            candidate.get('结构原型'),
            candidate.get('是否二维'),
            candidate.get('是否半导体')
        )
    print(candidate.get('化学式'), "-->", candidate_family)

    family_score = family_match_score(target_family, candidate_family)
    print(target_family, candidate_family, family_score)

    # Weighted sum
    total_score = (
        structure_score * weights.get('结构原型_权重', 0.4) +
        is_2d_score * weights.get('是否二维_权重', 0.1) +
        is_semiconductor_score * weights.get('是否半导体_权重', 0.1) +
        family_score * weights.get('材料族系_权重', 0.4)
    )

    return total_score


# ============================================================================
# Filtering Functions
# ============================================================================

def apply_hard_filters(
    materials: List[Dict],
    filter_conditions: Dict
) -> List[Dict]:
    """
    Apply hard filters at material level.

    Args:
        materials: List of material dictionaries
        filter_conditions: Filter conditions dictionary with keys:
            - 只看助熔剂法: bool (check if ANY recipe has "flux" in 生长方法)
            - 只看二维材料: bool (check if material's 是否二维 == true)
            - 只看半导体: bool (check if material's 是否半导体 == true)

    Returns:
        Filtered list of materials
    """
    filtered = []

    for material in materials:
        # Filter: 只看助熔剂法
        flux_filter = filter_conditions.get('只看助熔剂法')
        if flux_filter is True:
            has_flux = any(
                'flux' in recipe.get('工艺配方', {}).get('生长方法', '').lower()
                for recipe in material.get('配方列表', [])
            )
            if not has_flux:
                continue

        # Filter: 只看二维材料
        is_2d_filter = filter_conditions.get('只看二维材料')
        if is_2d_filter is True:
            if material.get('是否二维') is not True:
                continue
        elif is_2d_filter is False:
            if material.get('是否二维') is True:
                continue
        # If None, skip this filter

        # Filter: 只看半导体
        is_semiconductor_filter = filter_conditions.get('只看半导体')
        if is_semiconductor_filter is True:
            if material.get('是否半导体') is not True:
                continue
        elif is_semiconductor_filter is False:
            if material.get('是否半导体') is True:
                continue
        # If None, skip this filter

        filtered.append(material)

    logger.info(f"Filtered {len(materials)} materials to {len(filtered)} materials")
    return filtered


def filter_recipes_for_material(
    material: Dict,
    filter_conditions: Dict
) -> List[Dict]:
    """
    Filter recipes within a material based on conditions.

    Args:
        material: Material dictionary
        filter_conditions: Filter conditions dictionary

    Returns:
        Filtered list of recipes
    """
    filtered_recipes = []

    for recipe in material.get('配方列表', []):
        # Check flux method
        flux_filter = filter_conditions.get('只看助熔剂法')
        if flux_filter is True:
            method = recipe.get('工艺配方', {}).get('生长方法', '')
            if 'flux' not in method.lower():
                continue

        filtered_recipes.append(recipe)

    return filtered_recipes


# ============================================================================
# Output Formatting
# ============================================================================

def format_output(
    target_material: Dict,
    retrieval_params: Dict,
    similar_materials: List[Tuple[Dict, float]]
) -> Dict:
    """
    Format results to match similar_template.jsonl structure.

    Args:
        target_material: Target material from intent parsing
        retrieval_params: Retrieval parameters from intent parsing
        similar_materials: List of (material, similarity_score) tuples

    Returns:
        Formatted output dictionary
    """
    similar_materials_list = []

    for material, score in similar_materials:
        similar_materials_list.append({
            "材料ID": material.get('材料ID', ''),
            "化学式": material.get('化学式', ''),
            "结构原型": material.get('结构原型', ''),
            "材料标签": material.get('材料族系', []),
            "相似度": round(score, 4),
            "配方列表": material.get('配方列表', [])
        })

    output = {
        "目标材料": target_material,
        "检索参数": retrieval_params,
        "相似材料列表": similar_materials_list
    }

    return output


# ============================================================================
# Main Pipeline
# ============================================================================

def retrieve_similar_materials(
    query: str,
    kb_path: str,
    intuition_template: str,
    requirement_template: str,
    top_k: Optional[int] = None,
    query_use_path: bool = False,
    query_path: str = ""
) -> Dict:
    """
    Main retrieval pipeline for similar materials.

    Args:
        query: User query (e.g., "以 Mg 为助熔剂常压下生长 MgB₂ 单晶")
        kb_path: Path to knowledge base JSONL file
        top_k: Number of top similar materials to return (overrides intent)

    Returns:
        Dictionary matching similar_template.jsonl format
    """
    logger.info(f"Starting retrieval for query: {query}")

    # Step 1: Load pre-parsed intent (Claude handles intent parsing)
    logger.info("Loading pre-parsed intent...")
    if query_use_path and query_path:
        with open(query_path, 'r', encoding='utf-8') as f:
            intent = json.loads(f.read())
            logger.info(f"Loaded intent from {query_path}")
    else:
        logger.error(
            "Intent parsing requires Claude. "
            "Please provide pre-parsed intent via --query_use_path --query_path <file>."
        )
        sys.exit(1)

    # 从文件中读取相似度设置
    intent_settings = load_intuition_template(intuition_template)
    intent["相似度设置"] = intent_settings.get("相似度设置", {})
    intent["返回设置"] = intent_settings.get("返回设置", {})

    target_material = intent.get('目标材料', {})
    filter_conditions = intent.get('检索过滤条件', {})
    similarity_weights = intent.get('相似度设置', {})
    return_settings = intent.get('返回设置', {})

    # Override top_k if provided
    if top_k is None:
        top_k = return_settings.get('最多返回材料数_top_k', 10)

    logger.info(f"Target material: {target_material.get('化学式', 'Unknown')}")
    logger.info(f"Filter conditions: {filter_conditions}")
    logger.info(f"Top-k: {top_k}")

    # Step 2: Load knowledge base
    logger.info("Loading knowledge base...")
    materials = load_knowledge_base(kb_path)

    # Step 3: Apply hard filters at material level
    logger.info("Applying material-level filters...")
    filtered_materials = apply_hard_filters(materials, filter_conditions)

    if not filtered_materials:
        logger.warning("No materials passed the filters")
        return format_output(
            target_material,
            {
                "检索过滤条件": filter_conditions,
                "相似度设置": similarity_weights,
                "返回设置": return_settings
            },
            []
        )

    # Step 4: Calculate similarity scores
    logger.info("Calculating similarity scores...")
    scored_materials = []

    for material in filtered_materials:
        score = calculate_material_similarity(
            target_material,
            material,
            similarity_weights
        )
        scored_materials.append((material, score))

    # Step 5: Rank and select top-k
    logger.info("Ranking materials...")
    scored_materials.sort(key=lambda x: x[1], reverse=True)
    top_materials = scored_materials[:top_k]

    # Step 6: Filter recipes for each selected material
    logger.info("Filtering recipes for selected materials...")
    final_materials = []

    for material, score in top_materials:
        filtered_recipes = filter_recipes_for_material(material, filter_conditions)

        # Only include materials with at least one matching recipe
        if filtered_recipes:
            material_copy = material.copy()
            material_copy['配方列表'] = filtered_recipes
            final_materials.append((material_copy, score))

    logger.info(f"Final result: {len(final_materials)} materials with matching recipes")

    # Step 7: Format output
    output = format_output(
        target_material,
        {
            "检索过滤条件": filter_conditions,
            "相似度设置": similarity_weights,
            "返回设置": return_settings
        },
        final_materials
    )

    return output


# ============================================================================
# CLI Interface
# ============================================================================

def main():
    """CLI interface for testing."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Similar Material Retrieval System'
    )
    parser.add_argument(
        '--query',
        type=str,
        default="以 Mg 为助熔剂常压下生长 MgB₂ 单晶",
        help='User query'
    )
    parser.add_argument(
        '--query_path',
        type=str,
        default="data/similar_mates/intuition_tmp/query.jsonl",
        help='User query intent schema path (optional, relative to project root)'
    )
    parser.add_argument(
        '--query_use_path',
        action='store_true',
        help='Use path or not'
    )
    parser.add_argument(
        '--kb-path',
        type=str,
        default='data/knowledge_base/knowledge_base_processed.jsonl',
        help='Path to knowledge base JSONL file (relative to project root)'
    )
    parser.add_argument(
        '--intuition-template',
        type=str,
        default='data/similar_mates/intuition_template.jsonl',
        help='Path to intuition template JSONL file (relative to project root)'
    )
    parser.add_argument(
        '--requirement-template',
        type=str,
        default='data/similar_mates/how_to_parse_intuition.jsonl',
        help='Path to requirement template JSONL file (relative to project root)'
    )
    parser.add_argument(
        '--top-k',
        type=int,
        default=10,
        help='Number of top similar materials to return'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='data/similar_mates/similar_on-base_processed.jsonl',
        help='Output file path (optional, relative to project root)'
    )

    args = parser.parse_args()

    # Run retrieval
    result = retrieve_similar_materials(
        query=args.query,
        kb_path=args.kb_path,
        intuition_template=args.intuition_template,
        requirement_template=args.requirement_template,
        top_k=args.top_k,
        query_use_path=args.query_use_path,
        query_path=args.query_path
    )

    # Print or save result
    result_json = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(result_json)
        logger.info(f"Results saved to {args.output}")
    else:
        print(result_json)


if __name__ == '__main__':
    main()
