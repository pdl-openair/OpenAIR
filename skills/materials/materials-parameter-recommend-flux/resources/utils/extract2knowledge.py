#!/usr/bin/env python3
"""
Transform literature extraction structures to knowledge base storage structures.

This script processes JSONL files containing nested extraction structures and
transforms them into flattened knowledge base structures using LLM inference
for derived fields.
"""

import json
import logging
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

# Add parent directory to path to import reference_api
sys.path.insert(0, str(Path(__file__).parent.parent))
import os

# Runtime selection: prefer MATAI API if USE_MATAI_API enabled, else use reference_api
_USE_MATAI = str(os.getenv("USE_MATAI_API", "0")).lower() in ("1", "true", "yes", "on")
if _USE_MATAI:
    try:
        from utils.matai_api import get_chat_response
    except Exception:
        from src.reference_api import get_chat_response
else:
    from src.reference_api import get_chat_response

# ============================================================================
# Logging Configuration
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('outputs/extract2knowledge.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

DEFAULT_LLM_RESULTS = {
    "是否二维": None,
    "是否半导体": None,
    "实验目的": "",
    "原料摩尔比_标准化": "",
    "助熔剂家族标签": [],
    "助熔剂_对_溶质_摩尔比": None,
    "是否存在次高温预反应段": "不确定",
    "升温到次高温时间_h": None,
    "次高温段温度_摄氏": None,
    "次高温段保温时间_h": None,
    "升温到最高温时间_h": None,
    "最高温段保温温度_摄氏": None,
    "最高温段保温时间_h": None,
    "降温速率_主降温_℃每小时": None,
    "降温时间_主降温_h": None,
    "低温段保温温度_摄氏": None,
    "低温段保温时间_h": None,
    "冷却速率_至室温_标签": "",
    "分离温度_摄氏": None,
    "最大尺寸_mm": "",
    "形貌标签": "",
    "是否单晶": None
}

COMPREHENSIVE_PROMPT_TEMPLATE = """你是一位材料科学专家，专门分析晶体生长配方并提取结构化数据。请根据以下原始数据，推断和提取所有需要的字段。

**材料信息：**
- 化学式：{chemical_formula}
- 结构原型：{structure_type}
- 材料族系：{family}
- 维度描述：{dimensionality}

**生长方法：** {growth_method}

**原料：** {raw_materials}

**助熔剂信息：** {flux_info}

**原料摩尔比（原文）：** {molar_ratio_original}

**实验目的（英文）：** {experiment_purpose}

**温度程序原始数据：**
{temperature_profile_raw}

**分离信息：**
- 分离方法：{separation_method}
- 分离温度原文：{separation_temp_raw}

**晶体形态结构原文：** {crystal_morphology_raw}

---

**任务：请提取以下所有字段（严格按JSON格式输出）**

### 1. 材料属性
- **是否二维**：根据维度描述判断，输出 true（二维）/false（三维）/null（不确定）
- **是否半导体**：根据化学式和结构类型判断，输出 true/false/null

### 2. 配方字段
- **实验目的**：将英文翻译成中文，简洁专业
- **原料摩尔比_标准化**：标准化格式，如 "Ga:Na:C = 27:73:0.5"
- **助熔剂家族标签**：提取助熔剂类型标签数组，如 ["Na_flux"], ["Sn_flux"], ["Pb_flux"], ["self_flux"], ["alkali_halide"]，无法判断则为 []
- **助熔剂_对_溶质_摩尔比**：计算数值（如 Na:Ga=73:27 则为 2.7），无法计算则为 null

### 3. 温度程序（所有温度转为摄氏度，所有时间转为小时）
- **是否存在次高温预反应段**：判断是否有预反应阶段，输出 "是"/"否"/"不确定"
- **升温到次高温时间_h**：解析为小时数值或 null
- **次高温段温度_摄氏**：解析为摄氏度数值或 null
- **次高温段保温时间_h**：解析为小时数值或 null
- **升温到最高温时间_h**：解析为小时数值或 null
- **最高温段保温温度_摄氏**：解析为摄氏度数值或 null
- **最高温段保温时间_h**：解析为小时数值或 null
- **降温速率_主降温_℃每小时**：解析或计算降温速率（℃/h），"natural cooling"等文本描述则为 null
- **降温时间_主降温_h**：解析为小时数值或 null
- **低温段保温温度_摄氏**：解析为摄氏度数值或 null
- **低温段保温时间_h**：解析为小时数值或 null
- **冷却速率_至室温_标签**：映射为 "炉冷"（natural cooling/furnace cooling）/"快冷"（quench）/"水淬"（water quench）/""（未指定）
- **分离温度_摄氏**：从分离温度原文解析为摄氏度数值或 null

### 4. 晶体信息
- **最大尺寸_mm**：从形态描述中提取尺寸，格式如 "2×2×0.02"，无法提取则为 ""
- **形貌标签**：推断形貌标签，如 "plate_like"（板状）/"needle_like"（针状）/"bulk"（块状）/"pyramidal"（锥形），无法判断则为 ""
- **是否单晶**：判断是否为单晶，输出 true/false/null

---

**输出格式（仅输出JSON，不要任何解释或思考过程）：**
{{
  "是否二维": false,
  "是否半导体": true,
  "实验目的": "揭示生长过程中位错密度显著降低的机制",
  "原料摩尔比_标准化": "Ga:Na:C = 27:73:0.5",
  "助熔剂家族标签": ["Na_flux"],
  "助熔剂_对_溶质_摩尔比": 2.7,
  "是否存在次高温预反应段": "否",
  "升温到次高温时间_h": null,
  "次高温段温度_摄氏": null,
  "次高温段保温时间_h": null,
  "升温到最高温时间_h": 1.0,
  "最高温段保温温度_摄氏": 870.0,
  "最高温段保温时间_h": 96.0,
  "降温速率_主降温_℃每小时": null,
  "降温时间_主降温_h": null,
  "低温段保温温度_摄氏": null,
  "低温段保温时间_h": null,
  "冷却速率_至室温_标签": "炉冷",
  "分离温度_摄氏": null,
  "最大尺寸_mm": "",
  "形貌标签": "pyramidal",
  "是否单晶": true
}}
"""

# ============================================================================
# File I/O Functions
# ============================================================================

def load_jsonl(file_path: str, start_id: int = 1) -> List[Dict]:
    """Load JSONL file line by line, skip malformed lines."""
    records = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # 跳过file的前start_id行
            for line_num, line in enumerate(f, 1):
                if line_num < start_id:
                    continue
                
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    records.append(record)
                except json.JSONDecodeError as e:
                    logger.warning(f"Skipping malformed JSON at line {line_num}: {e}")
                    continue
        logger.info(f"Loaded {len(records)} records from {file_path}")
        return records
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return []
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return []


def load_template(template_path: str) -> Dict:
    """Load template.jsonl to get output structure."""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Remove JSON comments (// style)
        import re
        # Remove single-line comments
        content = re.sub(r'//.*?$', '', content, flags=re.MULTILINE)

        # Parse JSON
        template = json.loads(content)
        logger.info(f"Loaded template from {template_path}")
        return template
    except FileNotFoundError:
        logger.error(f"Template file not found: {template_path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in template file: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error loading template: {e}")
        return {}


def write_jsonl(file_path: str, records: List[Dict]) -> None:
    """Write records to JSONL file."""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        logger.info(f"Wrote {len(records)} records to {file_path}")
    except Exception as e:
        logger.error(f"Error writing to {file_path}: {e}")


def append_to_jsonl(file_path: str, record: Dict) -> None:
    """Append single record to JSONL file."""
    try:
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        logger.error(f"Error appending to {file_path}: {e}")


# ============================================================================
# Helper Functions
# ============================================================================

def safe_get(data: Dict, *keys, default="") -> Any:
    """Safely get nested dictionary value."""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key, {})
        else:
            return default
    return data if data else default


def safe_get_value(data: Dict, *keys, default="") -> Any:
    """Safely get nested dictionary value from 'value' field."""
    result = safe_get(data, *keys)
    if isinstance(result, dict):
        return result.get("value", default)
    return result if result else default


# ============================================================================
# Field Mapping Functions
# ============================================================================

def map_direct_fields(record: Dict) -> Dict:
    """Extract simple text fields that need no processing."""
    mapped = {}

    # Entry metadata
    mapped["文献ID"] = safe_get_value(record, "entry_meta", "doi", default="")

    # Material information
    mapped["化学式"] = safe_get_value(record, "material", "name", default="")
    mapped["结构原型"] = safe_get_value(record, "material", "structure_type", default="")

    # Method
    mapped["生长方法"] = safe_get_value(record, "method", default="")

    # Precursors - join element names
    elements = safe_get(record, "Steps", "Step1", "precursors", "elements", default=[])
    if isinstance(elements, list):
        element_names = [elem.get("name", "") for elem in elements if isinstance(elem, dict)]
        mapped["原料"] = ", ".join(filter(None, element_names))
    else:
        mapped["原料"] = ""

    # Flux info
    mapped["助熔剂信息"] = safe_get(record, "Steps", "Step1", "precursors", "flux_info", "source_text", default="")

    # Container
    mapped["容器"] = safe_get_value(record, "Steps", "Step2", "loading_container", default="")

    # Seeds
    mapped["籽晶"] = safe_get_value(record, "Steps", "Step1", "seeds", default="")

    # Molar ratio original
    mapped["原料摩尔比_原文"] = safe_get(record, "Steps", "Step1", "precursors", "molar_ratio", "source_text", default="")

    # Separation method
    mapped["分离方式"] = safe_get_value(record, "Steps", "Step4", "Speration", "Method", default="")

    # Crystal morphology
    mapped["晶体形态结构"] = safe_get(record, "Steps", "Step4", "crystal_info", "morphology_note", default="")

    # Further treatment
    mapped["晶体的进一步处理"] = safe_get(record, "Steps", "Step4", "further_treatment", "source_text", default="")

    return mapped


def extract_raw_data_for_llm(record: Dict) -> Dict:
    """Collect all raw data that LLM needs to process."""
    raw_data = {}

    # Material info
    raw_data["chemical_formula"] = safe_get_value(record, "material", "name", default="")
    raw_data["structure_type"] = safe_get_value(record, "material", "structure_type", default="")
    raw_data["family"] = safe_get_value(record, "material", "family", default="")
    raw_data["dimensionality"] = safe_get_value(record, "material", "dimensionality", default="")

    # Growth method
    raw_data["growth_method"] = safe_get_value(record, "method", default="")

    # Raw materials
    elements = safe_get(record, "Steps", "Step1", "precursors", "elements", default=[])
    if isinstance(elements, list):
        element_names = [elem.get("name", "") for elem in elements if isinstance(elem, dict)]
        raw_data["raw_materials"] = ", ".join(filter(None, element_names))
    else:
        raw_data["raw_materials"] = ""

    # Flux info
    raw_data["flux_info"] = safe_get(record, "Steps", "Step1", "precursors", "flux_info", "source_text", default="")

    # Molar ratio
    raw_data["molar_ratio_original"] = safe_get(record, "Steps", "Step1", "precursors", "molar_ratio", "source_text", default="")

    # Experiment purpose
    raw_data["experiment_purpose"] = safe_get_value(record, "experiment_purpose", default="")

    # Temperature profile - format as text
    temp_profile = safe_get(record, "Steps", "Step3", "temperature_profile", default={})
    temp_lines = []

    if isinstance(temp_profile, dict):
        # Heating up
        heating = temp_profile.get("heating_up", {})
        if isinstance(heating, dict):
            temp_lines.append(f"升温阶段: rate={heating.get('rate', '')}, target_temp={heating.get('target_temp', '')}, duration={heating.get('duration', '')}")
            temp_lines.append(f"  原文: {heating.get('source_text', '')}")

        # Soak high
        soak_high = temp_profile.get("soak_high", {})
        if isinstance(soak_high, dict):
            temp_lines.append(f"高温保温: temperature={soak_high.get('temperature', '')}, duration={soak_high.get('duration', '')}")
            temp_lines.append(f"  原文: {soak_high.get('source_text', '')}")

        # Cooling down
        cooling = temp_profile.get("cooling_down", {})
        if isinstance(cooling, dict):
            temp_lines.append(f"降温阶段: rate={cooling.get('rate', '')}, target_temp={cooling.get('target_temp', '')}, duration={cooling.get('duration', '')}")
            temp_lines.append(f"  原文: {cooling.get('source_text', '')}")

        # Soak low
        soak_low = temp_profile.get("soak_low", {})
        if isinstance(soak_low, dict):
            temp_lines.append(f"低温保温: temperature={soak_low.get('temperature', '')}, duration={soak_low.get('duration', '')}")
            temp_lines.append(f"  原文: {soak_low.get('source_text', '')}")

    raw_data["temperature_profile_raw"] = "\n".join(temp_lines) if temp_lines else "Not specified"

    # Separation info
    raw_data["separation_method"] = safe_get_value(record, "Steps", "Step4", "Speration", "Method", default="")
    raw_data["separation_temp_raw"] = safe_get_value(record, "Steps", "Step4", "Speration", "centrifugation_temperature", default="")

    # Crystal morphology
    raw_data["crystal_morphology_raw"] = safe_get(record, "Steps", "Step4", "crystal_info", "morphology_note", default="")

    return raw_data


# ============================================================================
# LLM Functions
# ============================================================================

def build_comprehensive_llm_prompt(raw_data: Dict) -> str:
    """Build comprehensive LLM prompt with all raw data."""
    return COMPREHENSIVE_PROMPT_TEMPLATE.format(**raw_data)


def call_llm_for_inference(prompt: str, max_retries: int = 3) -> Dict:
    """Call LLM API with retry logic."""
    system_prompt = '''你是材料科学专家，擅长分析晶体生长配方并提取结构化数据。请严格按照JSON格式输出，不要添加任何解释。
    ### 思考与输出强制规则：
    1. 思考要求：围绕「提取目标字段」做**简洁、专业、有逻辑的逐步推理**，仅保留核心推理步骤，不写冗余解释，总思考token控制在2048以内；
    2. 推理范围：仅基于提供的原始数据，除非要求不脑补未给出的信息，无法判断/计算的字段直接填null/""/[]；
    3. 输出要求：**仅输出符合格式的JSON内容，无任何前置/后置解释、思考步骤、备注**，JSON为唯一输出；
    4. 专业要求：材料学判断遵循专业常识，单位转换严格（K转℃：-273.15；min转h：÷60），摩尔比/速率计算精准。
    '''

    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(f"LLM inference attempt {attempt}/{max_retries}")

            response = get_chat_response(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.1,
                think_budget=2048,
                max_tokens=4096
            )

            if not response:
                logger.warning(f"Empty response from LLM (attempt {attempt})")
                continue

            # Parse JSON response
            from utils.response2json import response2json
            parsed = response2json(response)
            logger.debug("Successfully parsed LLM response")
            return parsed

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM JSON response (attempt {attempt}): {e}")
            logger.debug(f"Response was: {response if response else 'None'}")
            continue

        except Exception as e:
            logger.warning(f"LLM inference error (attempt {attempt}): {e}")
            continue

    # All retries failed
    logger.error(f"LLM inference failed after {max_retries} attempts, using default values")
    exit(1) # 直接退出，不留后路，防止默认错误数据干扰知识库
    return DEFAULT_LLM_RESULTS.copy()


def merge_llm_results(mapped_record: Dict, llm_results: Dict, template: Dict) -> Dict:
    """Merge all fields into template structure."""
    result = {}

    # Basic fields
    result["文献ID"] = mapped_record.get("文献ID", "")
    result["配方ID"] = mapped_record.get("配方ID", "")
    result["实验目的"] = llm_results.get("实验目的", "")

    # 目标产物信息
    result["目标产物信息"] = {
        "化学式": mapped_record.get("化学式", ""),
        "结构原型": mapped_record.get("结构原型", ""),
        "是否二维": llm_results.get("是否二维"),
        "是否半导体": llm_results.get("是否半导体")
    }

    # 工艺配方
    result["工艺配方"] = {
        "生长方法": mapped_record.get("生长方法", ""),
        "原料": mapped_record.get("原料", ""),
        "助熔剂信息": mapped_record.get("助熔剂信息", ""),
        "容器": mapped_record.get("容器", ""),
        "籽晶": mapped_record.get("籽晶", ""),
        "原料摩尔比_原文": mapped_record.get("原料摩尔比_原文", ""),
        "原料摩尔比_标准化": llm_results.get("原料摩尔比_标准化", ""),
        "助熔剂_对_溶质_摩尔比": llm_results.get("助熔剂_对_溶质_摩尔比"),
        "助熔剂家族标签": llm_results.get("助熔剂家族标签", [])
    }

    # 温度程序
    result["温度程序"] = {
        "是否存在次高温预反应段": llm_results.get("是否存在次高温预反应段", "不确定"),
        "升温到次高温时间_h": llm_results.get("升温到次高温时间_h"),
        "次高温段温度_摄氏": llm_results.get("次高温段温度_摄氏"),
        "次高温段保温时间_h": llm_results.get("次高温段保温时间_h"),
        "升温到最高温时间_h": llm_results.get("升温到最高温时间_h"),
        "最高温段保温温度_摄氏": llm_results.get("最高温段保温温度_摄氏"),
        "最高温段保温时间_h": llm_results.get("最高温段保温时间_h"),
        "降温速率_主降温_℃每小时": llm_results.get("降温速率_主降温_℃每小时"),
        "降温时间_主降温_h": llm_results.get("降温时间_主降温_h"),
        "低温段保温温度_摄氏": llm_results.get("低温段保温温度_摄氏"),
        "低温段保温时间_h": llm_results.get("低温段保温时间_h"),
        "冷却速率_至室温_标签": llm_results.get("冷却速率_至室温_标签", "")
    }

    # 分离与后处理
    result["分离与后处理"] = {
        "分离方式": mapped_record.get("分离方式", ""),
        "分离温度_摄氏": llm_results.get("分离温度_摄氏"),
        "晶体的进一步处理": mapped_record.get("晶体的进一步处理", "")
    }

    # 晶体信息
    result["晶体信息"] = {
        "晶体形态结构": mapped_record.get("晶体形态结构", ""),
        "最大尺寸_mm": llm_results.get("最大尺寸_mm", ""),
        "形貌标签": llm_results.get("形貌标签", ""),
        "是否单晶": llm_results.get("是否单晶")
    }

    return result


# ============================================================================
# Validation Functions
# ============================================================================

def validate_record(record: Dict) -> Tuple[bool, List[str]]:
    """Validate final record."""
    errors = []

    # Check required fields
    if not record.get("文献ID"):
        errors.append("Missing 文献ID")
    if not record.get("配方ID"):
        errors.append("Missing 配方ID")

    # Validate temperature ranges
    temp_program = record.get("温度程序", {})
    for field, value in temp_program.items():
        if "温度" in field and "标签" not in field:
            if value is not None and isinstance(value, (int, float)):
                if not (0 <= value <= 2000):
                    errors.append(f"Temperature out of range: {field}={value}")

    # Validate time values
    for field, value in temp_program.items():
        if ("时间" in field or "时间" in field) and value is not None:
            if isinstance(value, (int, float)) and value < 0:
                errors.append(f"Negative time value: {field}={value}")

    is_valid = len(errors) == 0
    return is_valid, errors


# ============================================================================
# Main Processing Functions
# ============================================================================

def process_single_record(record: Dict, recipe_id: int, template: Dict, skip_llm: bool = False) -> Dict:
    """Process a single record through the transformation pipeline."""
    try:
        # Step 1: Generate recipe ID
        mapped_record = {"配方ID": f"rec_{recipe_id:04d}"}

        # Step 2: Map direct fields
        mapped_record.update(map_direct_fields(record))

        # Step 3: Extract raw data for LLM
        raw_data = extract_raw_data_for_llm(record)

        # Step 4: Call LLM for inference
        if skip_llm:
            logger.debug(f"Skipping LLM for record {recipe_id}")
            llm_results = DEFAULT_LLM_RESULTS.copy()
        else:
            prompt = build_comprehensive_llm_prompt(raw_data)
            llm_results = call_llm_for_inference(prompt)

        # Step 5: Merge results
        final_record = merge_llm_results(mapped_record, llm_results, template)

        # Step 6: Validate
        is_valid, errors = validate_record(final_record)
        if not is_valid:
            logger.warning(f"Validation errors for {final_record.get('配方ID')}: {errors}")
            final_record["_validation_errors"] = errors

        return final_record

    except Exception as e:
        logger.error(f"Error processing record {recipe_id}: {e}")
        raise


def process_jsonl_file(input_path: str, output_path: str, template_path: str,
                       error_log_path: str, start_id: int = 1, skip_llm: bool = False, newout: bool = True) -> None:
    """Main processing loop."""
    # Load template
    template = load_template(template_path)
    if not template:
        logger.error("Failed to load template, aborting")
        return

    # Load input records
    records = load_jsonl(input_path, start_id=start_id)
    if not records:
        logger.error("No records to process")
        return

    # Initialize output file (clear existing content)
    try:
        if newout:
            with open(output_path, 'w', encoding='utf-8') as f:
                pass  # Create empty file
            logger.info(f"Initialized output file: {output_path}")
        else:
            logger.info(f"Using existing output file: {output_path}")
    except Exception as e:
        logger.error(f"Failed to initialize output file: {e}")
        return

    # Process records
    success_count = 0
    errors = []

    for idx, record in enumerate(records, start=start_id):
        # print(idx, record['entry_meta']['doi'])
        # input("press enter to continue")
        try:
            logger.info(f"Processing record {idx}/{start_id + len(records) - 1}")
            result = process_single_record(record, idx, template, skip_llm)

            # Write immediately after processing each record
            append_to_jsonl(output_path, result)
            success_count += 1
            logger.info(f"Saved record {idx} (total: {success_count} records)")

        except Exception as e:
            logger.error(f"Failed to process record {idx}: {e}")
            errors.append({
                "recipe_id": f"rec_{idx:04d}",
                "doi": safe_get_value(record, "entry_meta", "doi", default="unknown"),
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            })

    # Write error log
    if errors:
        try:
            with open(error_log_path, 'w', encoding='utf-8') as f:
                json.dump(errors, f, ensure_ascii=False, indent=2)
            logger.warning(f"Wrote {len(errors)} errors to {error_log_path}")
        except Exception as e:
            logger.error(f"Failed to write error log: {e}")

    logger.info(f"Processing complete: {success_count} records processed, {len(errors)} errors")


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Transform literature extraction structures to knowledge base format"
    )
    parser.add_argument("--input", required=True, help="Input JSONL path")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--template", required=True, help="Template JSONL path")
    parser.add_argument("--error-log", default="errors.json", help="Error log path")
    parser.add_argument("--start-id", type=int, default=1, help="Starting recipe ID")
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM inference")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--newout", action="store_true", help="New output file (overwrite existing)")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("Starting extract2knowledge transformation")
    logger.info(f"Input: {args.input}")
    logger.info(f"Output: {args.output}")
    logger.info(f"Template: {args.template}")
    logger.info(f"Skip LLM: {args.skip_llm}")
    logger.info("=" * 60)

    # Process files
    process_jsonl_file(
        input_path=args.input,
        output_path=args.output,
        template_path=args.template,
        error_log_path=args.error_log,
        start_id=args.start_id,
        skip_llm=args.skip_llm,
        newout=args.newout
    )

    logger.info("=" * 60)
    logger.info("Transformation complete!")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
