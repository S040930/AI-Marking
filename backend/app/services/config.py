"""系统配置读取服务。

从 ``system_config`` 表读取 key-value 配置项,提供给 OCR/LLM/prompt 等服务使用。
带 15 秒进程内缓存以降低 DB 压力,PUT 后主动失效。
"""

import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_config import SystemConfig

# 可配置项白名单(key -> description),供 API 校验与前端展示
CONFIG_KEYS: dict[str, str] = {
    "llm_api_key": "LLM API Key(OpenAI 兼容协议,支持豆包/通义/DeepSeek/OpenAI 等)",
    "llm_base_url": "LLM Base URL(留空使用默认 https://ark.cn-beijing.volces.com/api/v3)",
    "llm_model": "LLM Model / Endpoint ID(留空使用默认 doubao-pro-32k)",
    "paddleocr_api_url": "PaddleOCR-VL 文档解析 API URL(aistudio.baidu.com 控制台示例代码里复制)",
    "paddleocr_token": "PaddleOCR-VL Access Token(aistudio.baidu.com 个人访问令牌)",
    "rubric": "自定义评分标准(rubric,留空使用内置默认)",
    "llm_user_prompt": "自定义 LLM 用户提示词模板(留空使用内置默认,支持 {rubric}/{output_format}/{ocr_text} 占位符)",
}

# 缓存:15 秒 TTL(原 5s。PUT 已主动失效缓存,TTL 仅影响其他读路径的同步延迟,
# 15s 内单实例 4 并发可减少 ~80% 的 config 查询往返,且不影响配置写入后立即生效)
_CACHE_TTL = 15.0
_cache: dict = {"data": None, "expires_at": 0.0}


class ConfigError(Exception):
    """配置服务异常(未知 key 等)。"""


def invalidate_config_cache() -> None:
    """失效配置缓存。"""
    _cache["data"] = None
    _cache["expires_at"] = 0.0


async def get_config_dict(db: AsyncSession) -> dict[str, str]:
    """读取全部配置,返回 ``{key: value}`` 字典。

    带 5 秒内存缓存,缓存命中时直接返回;否则查表。
    表中不存在的 key 不出现在返回字典中,由调用方处理默认值。
    """
    now = time.time()
    if _cache["data"] is not None and now < _cache["expires_at"]:
        return _cache["data"]

    stmt = select(SystemConfig)
    rows = (await db.execute(stmt)).scalars().all()
    result = {row.key: row.value for row in rows}

    _cache["data"] = result
    _cache["expires_at"] = now + _CACHE_TTL
    return result


async def upsert_config(db: AsyncSession, updates: dict) -> dict[str, str]:
    """批量 upsert 配置项。

    Args:
        db: 数据库 AsyncSession
        updates: 待更新的 {key: value} 字典

    Returns:
        更新后的完整配置字典

    Raises:
        ConfigError: 出现未声明的 key
    """
    # 校验所有 key
    unknown = [k for k in updates.keys() if k not in CONFIG_KEYS]
    if unknown:
        raise ConfigError(f"未知配置项: {', '.join(unknown)}")

    # 查询现有记录
    stmt = select(SystemConfig).where(SystemConfig.key.in_(updates.keys()))
    existing = {row.key: row for row in (await db.execute(stmt)).scalars().all()}

    now = datetime.now()
    for key, value in updates.items():
        if value is None:
            continue
        if key in existing:
            existing[key].value = value
            existing[key].updated_at = now
        else:
            new_row = SystemConfig(
                key=key,
                value=value,
                description=CONFIG_KEYS[key],
                updated_at=now,
            )
            db.add(new_row)

    await db.commit()

    # 失效缓存并返回最新配置
    invalidate_config_cache()
    return await get_config_dict(db)


def get_config_value(config: dict[str, str], key: str, default: str = "") -> str:
    """从配置字典中取值,不存在或空时返回 default。"""
    value = config.get(key, "")
    return value if value else default
