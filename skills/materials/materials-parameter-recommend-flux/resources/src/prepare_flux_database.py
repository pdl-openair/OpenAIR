# -*- coding: utf-8 -*-
"""
数据预处理脚本：将obj_flux_9.json转换为flux_database_ready_for_rag.jsonl

功能：
1. 从多层嵌套JSON中提取所有带_comment的字段，扁平化为单层结构
2. 对objective字段生成embedding
3. 处理缺失字段（"Not specified"转为空字符串）
4. 输出为JSONL格式
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

# 添加src目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from reference_api import get_embedding


def normalize_value(value: Any) -> str:
    """将值标准化为字符串，处理"Not specified"和缺失情况"""
    if value is None:
        return ""
    if isinstance(value, str):
        if value.strip().lower() in ["not specified", "not specified (centrifugation not used)"]:
            return ""
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        # 如果是字典，尝试提取value或description字段
        if "value" in value:
            return normalize_value(value["value"])
        if "description" in value:
            return normalize_value(value["description"])
        # 否则返回空字符串
        return ""
    if isinstance(value, list):
        # 如果是列表，尝试提取第一个元素的value或name
        if value and isinstance(value[0], dict):
            if "value" in value[0]:
                return normalize_value(value[0]["value"])
            if "name" in value[0]:
                return normalize_value(value[0]["name"])
        # 否则将列表转为字符串
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def extract_elements_info(elements: list) -> str:
    """从elements列表中提取原料信息"""
    if not elements or not isinstance(elements, list):
        return ""
    
    parts = []
    for elem in elements:
        if not isinstance(elem, dict):
            continue
        name = elem.get("name", "")
        purity = elem.get("purity", "")
        form = elem.get("form", "")
        source = elem.get("source", "")
        
        # 跳过"Not specified"的字段
        elem_parts = []
        if name and name != "Not specified":
            elem_parts.append(f"名称: {name}")
        if purity and purity != "Not specified":
            elem_parts.append(f"纯度: {purity}")
        if form and form != "Not specified":
            elem_parts.append(f"性状: {form}")
        if source and source != "Not specified":
            elem_parts.append(f"来源: {source}")
        
        if elem_parts:
            parts.append(", ".join(elem_parts))
    
    return "; ".join(parts) if parts else ""


def extract_flux_info(flux_info: Any) -> str:
    """提取助熔剂信息"""
    if not flux_info:
        return ""
    if isinstance(flux_info, dict):
        if "description" in flux_info:
            return normalize_value(flux_info["description"])
        if "value" in flux_info:
            return normalize_value(flux_info["value"])
    return normalize_value(flux_info)


def extract_temperature_profile(profile: Dict[str, Any]) -> Dict[str, str]:
    """从temperature_profile中提取温度相关字段"""
    result = {}
    
    # 升温过程
    heating_up = profile.get("heating_up", {})
    if isinstance(heating_up, dict):
        result["升温到次高温时间"] = normalize_value(heating_up.get("duration", ""))
        result["次高温段温度"] = normalize_value(heating_up.get("target_temp", ""))
        result["升温到最高温时间"] = normalize_value(heating_up.get("duration", ""))
    
    # 高温段保温
    soak_high = profile.get("soak_high", {})
    if isinstance(soak_high, dict):
        result["最高温段保温温度"] = normalize_value(soak_high.get("temperature", ""))
        result["最高温段保温时间"] = normalize_value(soak_high.get("duration", ""))
    
    # 降温过程
    cooling_down = profile.get("cooling_down", {})
    if isinstance(cooling_down, dict):
        result["降温速率"] = normalize_value(cooling_down.get("rate", ""))
        result["降温时间"] = normalize_value(cooling_down.get("duration", ""))
    
    # 低温段保温
    soak_low = profile.get("soak_low", {})
    if isinstance(soak_low, dict):
        result["低温段保温温度"] = normalize_value(soak_low.get("temperature", ""))
        result["低温段保温时间"] = normalize_value(soak_low.get("duration", ""))
    
    return result


def extract_crystal_info(crystal_info: Any) -> str:
    """提取产物晶体信息"""
    if not crystal_info or not isinstance(crystal_info, dict):
        return ""
    
    parts = []
    if "crystal_size" in crystal_info:
        size = normalize_value(crystal_info["crystal_size"])
        if size:
            parts.append(f"尺寸: {size}")
    if "surface_quality" in crystal_info:
        quality = normalize_value(crystal_info["surface_quality"])
        if quality:
            parts.append(f"表面质量: {quality}")
    
    return "; ".join(parts) if parts else ""


def extract_further_treatment(treatment: Any) -> str:
    """提取晶体的进一步处理信息"""
    if not treatment or not isinstance(treatment, dict):
        return ""
    
    parts = []
    temp = normalize_value(treatment.get("temperature", ""))
    duration = normalize_value(treatment.get("duration", ""))
    env = normalize_value(treatment.get("environment", ""))
    apparatus = normalize_value(treatment.get("apparatus", ""))
    
    if temp:
        parts.append(f"温度: {temp}")
    if duration:
        parts.append(f"时间: {duration}")
    if env:
        parts.append(f"环境: {env}")
    if apparatus:
        parts.append(f"设备: {apparatus}")
    
    return "; ".join(parts) if parts else ""


def flatten_extract_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """将多层嵌套的抽取记录扁平化为单层结构"""
    flat = {}
    
    # 提取entry_meta
    entry_meta = record.get("entry_meta", {})
    flat["doi"] = entry_meta.get("doi", "")
    
    # 提取顶层字段
    material = record.get("material", {})
    flat["产物"] = normalize_value(material.get("name", "").get("value", ""))
    flat["产物结构原型"] = normalize_value(material.get("structure_type", "").get("value", ""))
    flat["产物所属族系"] = normalize_value(material.get("family", "").get("value", ""))
    flat["产物维度特征"] = normalize_value(material.get("dimensionality", "").get("value", ""))
    
    method = record.get("method", {})
    flat["生长方法"] = normalize_value(method.get("value", ""))
    
    objective = record.get("experiment_purpose", {})
    flat["目的"] = normalize_value(objective.get("value", ""))
    
    # 提取Steps中的字段
    steps = record.get("Steps", {})
    
    # Step1: 原料准备
    step1 = steps.get("Step1", {})
    precursors = step1.get("precursors", {})
    
    # 原料信息（从elements提取）
    elements = precursors.get("elements", [])
    flat["原料"] = extract_elements_info(elements)
    
    # 原料摩尔比
    molar_ratio = precursors.get("molar_ratio", {})
    flat["原料摩尔比"] = normalize_value(molar_ratio.get("value", ""))
    
    # 助熔剂信息
    flux_info = precursors.get("flux_info", {})
    flat["助熔剂信息"] = extract_flux_info(flux_info)
    
    # 籽晶
    seeds = step1.get("seeds", {})
    flat["籽晶"] = normalize_value(seeds.get("value", ""))
    
    # Step2: 装填和密封
    step2 = steps.get("Step2", {})
    flat["容器"] = normalize_value(step2.get("loading_container", {}).get("value", ""))
    flat["容器尺寸"] = normalize_value(step2.get("container_Size", {}).get("value", ""))
    flat["装填环境"] = normalize_value(step2.get("loading_envoriment", {}).get("value", ""))
    sealing_details = step2.get("sealing_details", {})
    flat["密封详情"] = normalize_value(sealing_details.get("description", ""))
    sealing_pressure = step2.get("sealing_pressure", {})
    flat["密封压力"] = normalize_value(sealing_pressure.get("description", ""))
    
    # Step3: 热处理
    step3 = steps.get("Step3", {})
    flat["炉型"] = normalize_value(step3.get("Furnace_type", {}).get("value", ""))
    
    # 温度曲线
    temp_profile = step3.get("temperature_profile", {})
    temp_fields = extract_temperature_profile(temp_profile)
    flat.update(temp_fields)
    
    # Step4: 晶体收获
    step4 = steps.get("Step4", {})
    separation = step4.get("Speration", {})
    separation_method = separation.get("Method", {})
    flat["分离方式"] = normalize_value(separation_method.get("value", ""))
    flat["分离温度"] = normalize_value(separation.get("centrifugation_temperature", {}).get("value", ""))
    
    # 进一步处理
    further_treatment = step4.get("further_treatment", {})
    flat["晶体的进一步处理"] = extract_further_treatment(further_treatment)
    
    # 产物晶体信息
    crystal_info = step4.get("crystal_info", {})
    flat["产物晶体的信息"] = extract_crystal_info(crystal_info)
    
    # 处理缺失字段，确保所有字段都存在
    required_fields = [
        "产物", "生长方法", "目的", "原料", "原料摩尔比", "助熔剂信息", "籽晶", "容器",
        "升温到次高温时间", "次高温段温度", "次高温段保温时间", "升温到最高温时间",
        "最高温段保温温度", "最高温段保温时间", "降温速率", "降温时间",
        "低温段保温温度", "低温段保温时间", "冷却速率", "分离方式", "分离温度",
        "晶体的进一步处理", "产物晶体的信息"
    ]
    
    for field in required_fields:
        if field not in flat:
            flat[field] = ""
    
    return flat


def process_flux_database(
    input_path: str = "data/extract_result/obj_flux_9.json",
    output_path: str = "data/rcmnd_database/flux_database_ready_for_rag.jsonl",
) -> None:
    """处理flux数据库，生成JSONL格式"""
    print(f"读取输入文件: {input_path}")

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            records = json.load(f)
    except:
        try:
            records = open(input_path, "r", encoding="utf-8").readlines()
            records = [json.loads(line) for line in records]
        except Exception as e:
            print(f"错误: 无法加载输入文件 {input_path}: {e}")
            return
    
    print(f"共 {len(records)} 条记录")
    
    output_records = []
    
    for idx, record in enumerate(records, 1):
        print(f"处理第 {idx}/{len(records)} 条记录...")
        record = json.loads(json.dumps(record))  # 确保是标准JSON对象
        
        # 扁平化提取字段
        flat_record = flatten_extract_record(record)
        
        # 添加ID
        flat_record["id"] = idx
        
        # 提取objective用于embedding
        objective = flat_record.get("目的", "")
        
        # 生成embedding
        print(f"  生成embedding...")
        embedding = get_embedding(objective)
        if embedding is None:
            print(f"  警告: 第 {idx} 条记录的embedding生成失败")
            embedding = []
        
        flat_record["embedding"] = embedding
        
        output_records.append(flat_record)
    
    # 写入JSONL文件
    print(f"写入输出文件: {output_path}")
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        for record in output_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    print(f"完成! 共生成 {len(output_records)} 条记录")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="处理flux数据库")
    parser.add_argument(
        "--input",
        type=str,
        default="data/extract_result/parsed_xunfei_responses_linejson.jsonl",
        help="输入JSON文件路径",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/rcmnd_database/flux_xunfei_database_ready_for_rag.jsonl",
        help="输出JSONL文件路径",
    )
    
    args = parser.parse_args()
    process_flux_database(args.input, args.output)
