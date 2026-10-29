import os
import logging
from openai import OpenAI
from dotenv import load_dotenv

# ================= 配置区域 =================
# 配置可通过环境变量传入（推荐）或放在项目根目录的 .env 文件中
# 支持的环境变量:
#   LLM_API_KEY
#   LLM_BASE_URL (OpenAI 兼容格式)
#   LLM_MODEL
#   EMBEDDING_MODEL
load_dotenv()

# API 配置（支持任意 OpenAI 兼容 API）
API_KEY = os.getenv("LLM_API_KEY", "sk-xxx")
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.siliconflow.cn/v1")

# Embedding 模型配置
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")

# LLM 模型配置
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# ===========================================

# 初始化客户端
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
logger = logging.getLogger(__name__)

def get_embedding(text):
    """
    [Embedding] 输入文本，返回向量 (List[float])
    """
    if not text or not isinstance(text, str):
        return None

    # 预处理：将换行符替换为空格
    text = text.replace("\n", " ")

    try:
        response = client.embeddings.create(
            input=[text],
            model=EMBEDDING_MODEL,
            encoding_format="float"
        ) # 获取embedding的方式
        return response.data[0].embedding
    except Exception as e:
        print(f"Embedding API Error: {e}")
        return None

def get_chat_response(prompt, system_prompt="你是一个有用的助手。", temperature=0.7, think_budget=4096, max_tokens=8192):
    """
    [LLM] 输入提示词，返回模型回复文本
    
    参数:
        prompt (str): 用户的输入
        system_prompt (str): 系统设定
        temperature (float): 随机性 (0-1)
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]
    logger.info("======= 提示词 ========")
    logger.info(messages)

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens, # 最大输出长度 # NOTE: 如果设置成和thinking_budget一样，则如思考过长（出bug了），就不会有content
            
            # SiliconFlow 特有参数需要通过 extra_body 传入
            extra_body={
                # "enable_thinking": True,   # 启用思考模式
                "thinking_budget": think_budget,   # 思考预算 token 数
                "min_p": 0.05              # Qwen系列推荐参数
            }
        )
        # 友好日志：元信息 + 内容预览
        try:
            usage = getattr(response, "usage", None)
            choice0 = response.choices[0]
            logger.info(
                "LLM resp meta: finish=%s, prompt_tokens=%s, completion_tokens=%s, total_tokens=%s",
                choice0.finish_reason,
                getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None),
                getattr(usage, "total_tokens", None),
            )
        except Exception:
            logger.info("LLM resp meta: (unavailable)")

        # 获取回复内容或 reasoning_content 兜底
        message = response.choices[0].message
        content = getattr(message, "content", None)
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            r_preview = str(reasoning)
            logger.info("======== 推理过程 ========")
            logger.info(r_preview)
            # input("按回车键继续...")
            # logger.info("LLM reasoning preview: %s", r_preview[:400].replace("\n", "\\n"))
        if content:
            preview = str(content)
            logger.info("======== 最终回答 ========")
            logger.info(preview)
            # logger.info("LLM content preview: %s", preview[:400].replace("\n", "\\n"))
        if (not content or not str(content).strip()) and reasoning:
            logger.info("LLM content为空，使用reasoning_content兜底")
            content = reasoning

        return content

    except Exception as e:
        print(f"LLM API Error: {e}")
        return None

# ================= 测试代码 =================
if __name__ == "__main__":
    print("-" * 30)
    print("1. 测试 Embedding...")
    vec = get_embedding("测试文本")
    if vec:
        print(f"Embedding 成功! 维度: {len(vec)}")
    else:
        print("Embedding 失败")

    print("-" * 30)
    print(f"2. 测试 LLM ({LLM_MODEL})...")
    answer = get_chat_response("中国的陶瓷材料发展历史简述", system_prompt="你是一个材料学专家")
    
    if answer:
        print("LLM 回复成功:")
        print(answer[:200] + "...") # 只打印前200个字预览
    else:
        print("LLM 回复失败")
    print("-" * 30)
