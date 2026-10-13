"""Provider 配置读写:``system_config`` 键的封装,密钥写不读回。

- 读:``get_provider_config`` 返回全量(含密钥明文),仅供进程内构造
  provider 使用;任何对外输出必须走 ``provider_summary``(只给
  configured 与 last4,绝不回显密钥本体)。
- 写:``set_provider_config`` 支持三种语义——值为 ``None`` 不改动、
  空串清除该键、非空则 upsert;未知键拒绝。
"""

from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.system_config import SystemConfig

PROVIDER_DEFAULT_KEY = "provider_default"
OPENAI_API_KEY_KEY = "openai_api_key"
OPENAI_BASE_URL_KEY = "openai_base_url"
OPENAI_DEFAULT_MODEL_KEY = "openai_default_model"
OPENAI_REASONING_EFFORT_KEY = "openai_reasoning_effort"
CHATGPT_TOKENS_KEY = "chatgpt_tokens"
CHATGPT_DEFAULT_MODEL_KEY = "chatgpt_default_model"

PROVIDER_OPENAI_API = "openai_api"
PROVIDER_CHATGPT_SUB = "chatgpt_sub"

PROVIDER_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        PROVIDER_DEFAULT_KEY,
        OPENAI_API_KEY_KEY,
        OPENAI_BASE_URL_KEY,
        OPENAI_DEFAULT_MODEL_KEY,
        OPENAI_REASONING_EFFORT_KEY,
        CHATGPT_TOKENS_KEY,
        CHATGPT_DEFAULT_MODEL_KEY,
    }
)

# 永不回显的键(只报告 configured 与 last4)
SECRET_KEYS: frozenset[str] = frozenset({OPENAI_API_KEY_KEY, CHATGPT_TOKENS_KEY})


class ProviderConfigError(Exception):
    """Provider 配置服务异常(未知 key 等)。"""


def get_provider_config(db: Session) -> dict[str, str]:
    """读取全部 provider 配置(含密钥明文);仅供进程内构造 provider。"""
    rows = db.execute(
        select(SystemConfig).where(SystemConfig.key.in_(PROVIDER_CONFIG_KEYS))
    ).scalars()
    return {row.key: row.value for row in rows}


def set_provider_config(db: Session, updates: dict[str, object]) -> dict[str, str]:
    """批量写入;``None`` 不改动,空串清除,非空 upsert。"""
    unknown = [key for key in updates if key not in PROVIDER_CONFIG_KEYS]
    if unknown:
        raise ProviderConfigError(f"未知 provider 配置项: {', '.join(unknown)}")
    existing = {
        row.key: row
        for row in db.execute(
            select(SystemConfig).where(SystemConfig.key.in_(updates.keys()))
        ).scalars()
    }
    for key, value in updates.items():
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True
        )
        if text == "":
            row = existing.get(key)
            if row is not None:
                db.execute(delete(SystemConfig).where(SystemConfig.key == key))
            continue
        row = existing.get(key)
        if row is not None:
            row.value = text
        else:
            db.add(SystemConfig(key=key, value=text))
    db.commit()
    return get_provider_config(db)


def provider_default(db: Session) -> str:
    return get_provider_config(db).get(PROVIDER_DEFAULT_KEY) or PROVIDER_OPENAI_API


def provider_summary(db: Session) -> dict:
    """对外可安全展示的通道状态:configured/last4,绝不回显密钥。"""
    config = get_provider_config(db)
    openai_key = config.get(OPENAI_API_KEY_KEY) or ""
    chatgpt_raw = config.get(CHATGPT_TOKENS_KEY) or ""
    return {
        "provider_default": config.get(PROVIDER_DEFAULT_KEY) or PROVIDER_OPENAI_API,
        "providers": {
            PROVIDER_OPENAI_API: {
                "configured": bool(openai_key),
                "key_last4": _last4(openai_key),
                "base_url": config.get(OPENAI_BASE_URL_KEY) or "",
                "default_model": config.get(OPENAI_DEFAULT_MODEL_KEY) or "",
                "reasoning_effort": config.get(OPENAI_REASONING_EFFORT_KEY) or "",
            },
            PROVIDER_CHATGPT_SUB: {
                "configured": bool(chatgpt_raw),
                "key_last4": None,
                "default_model": config.get(CHATGPT_DEFAULT_MODEL_KEY) or "",
                "available": False,
            },
        },
    }


def _last4(secret: str) -> str | None:
    if not secret:
        return None
    tail = secret[-4:]
    return f"…{tail}" if len(secret) > 4 else "…"
