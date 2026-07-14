"""系统配置读取服务。

从 ``system_config`` 表读取 key-value 配置项,提供给 OCR/LLM/prompt 等服务使用。
带 15 秒进程内缓存以降低 DB 压力,PUT 后主动失效。
"""

import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system_config import SystemConfig

# 可配置项白名单,供 API 校验。各 key 的语义说明见 schemas/system_config.py
# 中 ConfigUpdate 各字段的 description。
CONFIG_KEYS: frozenset[str] = frozenset({
    "llm_api_key",
    "llm_base_url",
    "llm_model",
    "review_llm_api_key",
    "review_llm_base_url",
    "review_llm_model",
    "paddleocr_api_url",
    "paddleocr_token",
    "rubric",
    "llm_user_prompt",
    "operator_name",
})

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


def get_config_dict(db: Session) -> dict[str, str]:
    """读取全部配置,返回 ``{key: value}`` 字典。

    带 5 秒内存缓存,缓存命中时直接返回;否则查表。
    表中不存在的 key 不出现在返回字典中,由调用方处理默认值。
    """
    now = time.time()
    if _cache["data"] is not None and now < _cache["expires_at"]:
        return _cache["data"]

    stmt = select(SystemConfig)
    rows = db.execute(stmt).scalars().all()
    result = {row.key: row.value for row in rows}

    _cache["data"] = result
    _cache["expires_at"] = now + _CACHE_TTL
    return result


def upsert_config(db: Session, updates: dict) -> dict[str, str]:
    """批量 upsert 配置项。

    Args:
        db: 数据库 Session
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
    existing = {row.key: row for row in db.execute(stmt).scalars().all()}

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
                updated_at=now,
            )
            db.add(new_row)

    db.commit()

    # 失效缓存并返回最新配置
    invalidate_config_cache()
    return get_config_dict(db)
