"""系统配置读取服务。

系统仅维护一份全局配置,从 ``system_config`` 表读取 key-value 配置项,
提供给 OCR/LLM/prompt 等服务使用。
配置表规模很小，所有进程直接读取数据库，确保 API 与 worker 立即看到同一配置。
"""

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system_config import SystemConfig

# 可配置项白名单,供 API 校验。各 key 的语义说明见 schemas/system_config.py
# 中 ConfigUpdate 各字段的 description。
# 收敛为 MCP-only 后不再有 LLM/Agent 相关配置项;OCR、结构化 rubric 与
# MCP 双遍自检开关(固定必需)保留。
CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "paddleocr_api_url",
        "paddleocr_token",
        "rubric_definition",
        "review_enabled",
    }
)


class ConfigError(Exception):
    """配置服务异常(未知 key 等)。"""


def get_config_dict(db: Session) -> dict[str, str]:
    """读取全部配置,返回 ``{key: value}`` 字典。"""
    rows = db.execute(select(SystemConfig)).scalars().all()
    return {row.key: row.value for row in rows}


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
        if key == "rubric_definition" and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif not isinstance(value, str):
            # SystemConfig.value 是 Text 列,布尔等非字符串配置统一转字符串
            value = str(value).lower()
        if key in existing:
            existing[key].value = value
            existing[key].updated_at = now
        else:
            db.add(SystemConfig(key=key, value=value, updated_at=now))

    db.commit()

    return get_config_dict(db)
