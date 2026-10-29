'''
写个函数，接收用户query作为输入，用LLM进行意图解析，返回如data/similar_mates/input_example.jsonl格式的数据
示例query：以 Mg 为助熔剂常压下生长 MgB₂ 单晶
'''

import json
import re
import logging
from typing import Dict
from pathlib import Path
from datetime import datetime
import sys

# Add src to path for reference_api import
sys.path.append(str(Path(__file__).parent.parent))
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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_intuition_template(template_path="data/similar_mates/intuition_template.jsonl") -> Dict:
    """Load intuition template from JSONL file, stripping JSON comments."""

    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Remove JSON comments (// style)
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


def load_requirements_template(requirements_path="data/similar_mates/how_to_parse_intuition.jsonl") -> str:
    """Load how_to_parse_intuition.jsonl with comments for prompt generation."""

    try:
        with open(requirements_path, 'r', encoding='utf-8') as f:
            content = f.read()

        logger.info(f"Loaded requirements template from {requirements_path}")
        return content

    except FileNotFoundError:
        logger.error(f"Requirements file not found: {requirements_path}")
        exit(1)
        return ""
    except Exception as e:
        logger.error(f"Error loading requirements: {e}")
        exit(1) 
        return ""


def build_system_prompt(intuition_template="data/similar_mates/intuition_template.jsonl", 
                         requirement_template="data/similar_mates/how_to_parse_intuition.jsonl") -> str:
    """Build comprehensive system prompt dynamically from how_to_parse_intuition.jsonl."""
    requirements_content = load_requirements_template(requirement_template)

    if not requirements_content:
        logger.warning("Failed to load requirements, using fallback prompt")
        return "你是一位材料科学专家，请从用户查询中提取结构化的实验意图信息，输出JSON格式。"

    # Load the clean template for example output
    template = load_intuition_template(intuition_template)

    if not template:
        logger.error("Failed to load clean template")
        exit(1)
        return "你是一位材料科学专家，请从用户查询中提取结构化的实验意图信息，输出JSON格式。"

    # Build the prompt with the full template including comments
    prompt = f"""你是一位材料科学专家，专门研究助熔剂法晶体生长。你的任务是从用户的查询中提取结构化的实验意图信息。

**提取字段说明（严格按照以下要求）：**

以下是完整的字段模板及其要求。每个字段后的 // 注释说明了字段的含义、类型、默认值和提取规则：

```jsonc
{requirements_content}
```

**重要提示：**
1. 仔细阅读每个字段后的注释（// 开头），了解字段含义、类型、默认值和提取规则
2. 布尔值必须是 true/false/null，不能是字符串
3. 所有权重使用模板中的默认值

**输出要求：**
- 仅输出标准JSON格式（去掉所有 // 注释和以 "//" 开头的注释字段）
- 不要任何解释或思考过程
- 所有字段必须存在，使用注释中指定的默认值
- 输出的JSON必须是有效的标准JSON，不包含注释

**输出示例（标准JSON，无注释）：**
```json
{json.dumps(template, ensure_ascii=False, indent=2)}
```
"""

    return prompt


def call_llm_with_retry(prompt: str, system_prompt: str, max_retries: int = 3) -> Dict:
    """Call LLM API with retry logic for intent parsing."""

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"LLM inference attempt {attempt}/{max_retries}")

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
            logger.info("Successfully parsed LLM response")
            return parsed

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM JSON response (attempt {attempt}): {e}")
            logger.debug(f"Response was: {response[:500] if response else 'None'}")
            continue

        except Exception as e:
            logger.warning(f"LLM inference error (attempt {attempt}): {e}")
            continue

    # All retries failed - return template with defaults
    logger.error(f"LLM inference failed after {max_retries} attempts, using default template")
    exit(1)  # 直接退出，不留后路，防止默认错误数据干扰知识库
    return 


def parse_user_intuition(query: str, intuition_template="data/similar_mates/intuition_template.jsonl", 
                         requirement_template="data/similar_mates/how_to_parse_intuition.jsonl") -> Dict:
    """
    Parse user query and extract structured intent data using LLM.

    Args:
        query: User query string (e.g., "以 Mg 为助熔剂常压下生长 MgB₂ 单晶")

    Returns:
        Dict containing structured intent data matching the template format
    """
    # Input validation
    if not query or not isinstance(query, str) or not query.strip():
        logger.error("Invalid query: must be non-empty string")
        exit(1)
        return load_intuition_template(intuition_template)

    logger.info(f"Parsing user query: {query}")

    # Build prompts
    system_prompt = build_system_prompt(intuition_template, requirement_template)
    user_prompt = f"""用户查询：{query}

请根据上述查询，提取所有字段并输出JSON格式结果。"""

    # Call LLM with retry logic
    result = call_llm_with_retry(user_prompt, system_prompt, max_retries=2)

    # Save result to timestamped file
    output_dir = Path(__file__).parent.parent / "data" / "similar_mates" / "intuition_tmp"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = output_dir / f"{timestamp}.jsonl"

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')
        logger.info(f"Saved result to {output_path}")
    except Exception as e:
        logger.error(f"Failed to save result: {e}")
        exit(1)

    return result


if __name__ == "__main__":
    print("=" * 60)
    print("Testing User Intent Parsing Function")
    print("=" * 60)

    # Test with example query
    test_query = "以 Mg 为助熔剂常压下生长 MgB₂ 单晶"
    print(f"\nTest Query: {test_query}\n")

    result = parse_user_intuition(test_query)

    print("\nParsed Result:")
    print("-" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("-" * 60)

    # Validate structure
    print("\nValidation:")
    required_keys = ["目标材料", "实验目标", "检索过滤条件", "相似度设置", "返回设置"]
    for key in required_keys:
        if key in result:
            print(f"✓ {key}: Present")
        else:
            print(f"✗ {key}: Missing")

    # Check target material fields
    if "目标材料" in result:
        target = result["目标材料"]
        print(f"\n目标材料.化学式: {target.get('化学式', 'MISSING')}")

    print("\n" + "=" * 60)
