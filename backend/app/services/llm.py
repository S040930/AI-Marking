"""LLM 批改服务模块(OpenAI 兼容协议)。

通过 OpenAI SDK 兼容方式调用任意 OpenAI 格式的 LLM API
(豆包/通义/DeepSeek/OpenAI 官方 等),
基于 rubric 对 OCR 文本进行批改并返回结构化 JSON。

API Key / Base URL / Model 由调用方(批改流水线)从数据库读取后传入,
不再依赖 ``settings`` 中的环境变量。
"""

import json
import logging

from openai import AsyncOpenAI

from app.core.prompt import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

# 默认值(配置项为空时回退使用;默认指向豆包,与原 MVP 一致)
DEFAULT_LLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_LLM_MODEL = "doubao-pro-32k"


class LLMError(Exception):
    """LLM 批改服务异常。"""


def _get_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """创建 OpenAI 兼容 async client。"""
    if not api_key:
        raise LLMError("LLM API Key 未配置,请在设置页填写")
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url or DEFAULT_LLM_BASE_URL,
    )


async def mark_submission(ocr_text: str, config: dict) -> dict:
    """调用 LLM 批改作业,返回结构化结果。

    Args:
        ocr_text: OCR 解析后的作业文本
        config: 配置字典,需包含 ``llm_api_key``、``llm_base_url``(可选)、
            ``llm_model``(可选)、``rubric``(可选)

    Returns:
        {"score": float, "feedback": str,
         "details": [{"criterion", "score", "comment"}]}

    Raises:
        LLMError: API 错误、JSON 解析失败、字段缺失
    """
    api_key = config.get("llm_api_key", "") or ""
    base_url = config.get("llm_base_url", "") or ""
    model = config.get("llm_model", "") or DEFAULT_LLM_MODEL
    rubric = config.get("rubric", "") or ""
    user_prompt_template = config.get("llm_user_prompt", "") or ""

    client = _get_client(api_key, base_url)
    prompt = build_user_prompt(
        ocr_text,
        rubric=rubric or None,
        user_prompt_template=user_prompt_template or None,
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            # 要求 JSON 输出(大多数 OpenAI 兼容服务支持 response_format)
            response_format={"type": "json_object"},
            temperature=0.3,
        )
    except Exception as e:
        raise LLMError(f"LLM API 调用失败: {e}") from e

    raw_content = response.choices[0].message.content or ""

    # 解析 JSON
    try:
        result = json.loads(raw_content)
    except json.JSONDecodeError as e:
        logger.error("LLM 响应非合法 JSON: %s", raw_content[:500])
        raise LLMError(f"LLM 响应解析失败: {e}") from e

    # 校验字段
    required = {"score", "feedback", "details"}
    if not required.issubset(result.keys()):
        missing = required - result.keys()
        logger.error("LLM 响应缺失字段: %s, 原始: %s", missing, raw_content[:500])
        raise LLMError(f"LLM 响应缺失字段: {missing}")

    # 校验 details 结构
    if not isinstance(result["details"], list):
        raise LLMError("LLM 响应 details 必须为数组")

    return result
