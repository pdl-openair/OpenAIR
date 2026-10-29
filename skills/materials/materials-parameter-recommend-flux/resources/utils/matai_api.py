import os
import sys
import logging
from matai import MatAI

# Configure logging from environment
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logging.basicConfig(format="%(name)s - %(levelname)s - %(message)s")

# Client configuration from environment
MATAI_BASE_URL = os.getenv("MATAI_BASE_URL", "202.85.209.150:9979")
try:
    MATAI_TIMEOUT = int(os.getenv("MATAI_TIMEOUT", "360"))
except Exception:
    MATAI_TIMEOUT = 360

NONINTERACTIVE = os.getenv("MATAI_NONINTERACTIVE", "0").lower() in ("1", "true", "yes")

client = MatAI(base_url=MATAI_BASE_URL, timeout=MATAI_TIMEOUT)


def _maybe_input(prompt: str = ""):
    """Prompt for input only when interactive and not forced non-interactive."""
    if NONINTERACTIVE:
        logger.debug("Skipping interactive prompt (MATAI_NONINTERACTIVE set)")
        return
    try:
        if sys.stdin and sys.stdin.isatty():
            input(prompt)
    except Exception:
        logger.debug("Input prompt skipped (non-tty or error)")


def get_chat_response(prompt, system_prompt="你是一个有用的助手。", temperature=0.7, think_budget=4096, max_tokens=4096):
    """
    [LLM] 输入提示词，返回模型回复文本
    
    参数:
        prompt (str): 用户的输入
        system_prompt (str): 系统设定
        temperature (float): 随机性 (0-1)
    """
    prompt = system_prompt + "\n=========\n" + prompt
    token_info = client.tokens(prompt)
    logger.info("======== 提示词 ========")
    logger.info(prompt)
    logger.info("提示词Token数量: %s", token_info.get('payload', {}).get('tokens'))
    _maybe_input("按回车键继续...")

    messages = [
        # {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    try:
        response = client.chat.create(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens, # 最大输出长度
            top_p=0.05,
            think=True
        )
        logger.info("LLM finish code: %s", response.get('header', {}).get('code'))
        payload = response.get('payload', {})
        # 友好日志：元信息 + 内容预览
        try:
            logger.info(
                "LLM resp meta: prompt_tokens=%s, completion_tokens=%s, total_tokens=%s",
                response.get('payload', {}).get('usage', {}).get('text', {}).get('prompt_tokens'),
                response.get('payload', {}).get('usage', {}).get('text', {}).get('completion_tokens'),
                response.get('payload', {}).get('usage', {}).get('text', {}).get('total_tokens'),
            )
            _maybe_input("按回车键继续...")
        except Exception:
            logger.info("LLM resp meta: (unavailable)")
        # 获取回复内容或 reasoning_content 兜底
        choices = payload.get('choices', {}).get('text', [])
        if choices and len(choices) > 0:
            content = choices[0].get('content')
            reasoning = choices[0].get('reasoning')
        else:
            content = None
            reasoning = None

        if reasoning:
            r_preview = str(reasoning)
            logger.info("======== 推理过程 ========")
            logger.info(r_preview)
            _maybe_input("按回车键继续...")
            # logger.info("LLM reasoning preview: %s", r_preview[:400].replace("\n", "\\n"))
        if content:
            preview = str(content)
            logger.info("======== 最终回答 ========")
            logger.info(preview)
            _maybe_input("按回车键继续...")
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
    print(f"2. 测试 LLM ...")
    answer = get_chat_response("氮化镓的带隙宽度是多少？", system_prompt="你是一个材料学专家")
    
    if answer:
        print("LLM 回复成功:")
        print(answer[:200] + "...") # 只打印前200个字预览
    else:
        print("LLM 回复失败")
    print("-" * 30)
