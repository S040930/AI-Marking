"""系统配置读取服务。

配置按「配置项目」(``ConfigProfile``) 组织:每套 LLM/OCR/Rubric/提示词
配置归属一个项目,题目通过 ``Question.config_profile_id`` 绑定使用的项目。
从 ``system_config`` 表读取 key-value 配置项,提供给 OCR/LLM/prompt 等服务使用。
带 15 秒进程内缓存(按 profile 分桶)以降低 DB 压力,PUT 后主动失效。
"""

import json
import time
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.config_profile import ConfigProfile
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

# 缓存:15 秒 TTL(原 5s。PUT 已主动失效缓存,TTL 仅影响其他读路径的同步延迟,
# 15s 内单实例 4 并发可减少 ~80% 的 config 查询往返,且不影响配置写入后立即生效)
_CACHE_TTL = 15.0
# 按 profile_id 分桶:{"data": {profile_id: {...}}, "expires_at": {profile_id: ts}}
_cache: dict = {"data": {}, "expires_at": {}}

DEFAULT_PROFILE_NAME = "默认配置"


class ConfigError(Exception):
    """配置服务异常(未知 key 等)。"""


def invalidate_config_cache() -> None:
    """失效全部配置缓存。"""
    _cache["data"] = {}
    _cache["expires_at"] = {}


def invalidate_profile_cache(profile_id: int) -> None:
    """失效单个配置项目的缓存。"""
    _cache["data"].pop(profile_id, None)
    _cache["expires_at"].pop(profile_id, None)


def get_default_profile(db: Session) -> ConfigProfile | None:
    """读取当前的默认配置项目(只读,不存在返回 None)。"""
    return (
        db.execute(select(ConfigProfile).where(ConfigProfile.is_default.is_(True)))
        .scalars()
        .first()
    )


def get_or_create_default_profile(db: Session) -> ConfigProfile:
    """获取默认配置项目,不存在时(如全新数据库)自动创建。

    默认项目保证服务永远有可用配置集合,避免题目绑定空指针。
    """
    profile = get_default_profile(db)
    if profile is not None:
        return profile
    profile = ConfigProfile(name=DEFAULT_PROFILE_NAME, is_default=True)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_profile(db: Session, profile_id: int) -> ConfigProfile | None:
    """按 ID 读取配置项目。"""
    return db.get(ConfigProfile, profile_id)


def list_profiles(db: Session) -> list[ConfigProfile]:
    """列出全部配置项目(默认项目优先,再按创建时间)。"""
    return list(
        db.execute(
            select(ConfigProfile).order_by(
                ConfigProfile.is_default.desc(), ConfigProfile.created_at.asc()
            )
        )
        .scalars()
        .all()
    )


def create_profile(
    db: Session, name: str, *, copy_from_id: int | None = None
) -> ConfigProfile:
    """新建配置项目。

    Args:
        name: 项目名称(唯一)
        copy_from_id: 非空时从该项目复制全套配置值
    """
    name = name.strip()
    if not name:
        raise ConfigError("配置项目名称不能为空")
    existing = (
        db.execute(select(ConfigProfile).where(ConfigProfile.name == name))
        .scalars()
        .first()
    )
    if existing is not None:
        raise ConfigError(f"配置项目名称已存在: {name}")

    profile = ConfigProfile(name=name, is_default=False)
    db.add(profile)
    db.flush()

    if copy_from_id is not None:
        source = db.get(ConfigProfile, copy_from_id)
        if source is None:
            db.rollback()
            raise ConfigError("要复制的配置项目不存在")
        source_rows = (
            db.execute(
                select(SystemConfig).where(SystemConfig.profile_id == copy_from_id)
            )
            .scalars()
            .all()
        )
        now = datetime.now()
        for row in source_rows:
            db.add(
                SystemConfig(
                    profile_id=profile.id,
                    key=row.key,
                    value=row.value,
                    updated_at=now,
                )
            )

    db.commit()
    db.refresh(profile)
    return profile


def rename_profile(db: Session, profile_id: int, name: str) -> ConfigProfile:
    """重命名配置项目。"""
    name = name.strip()
    if not name:
        raise ConfigError("配置项目名称不能为空")
    profile = db.get(ConfigProfile, profile_id)
    if profile is None:
        raise ConfigError("配置项目不存在")
    clash = (
        db.execute(
            select(ConfigProfile).where(
                ConfigProfile.name == name, ConfigProfile.id != profile_id
            )
        )
        .scalars()
        .first()
    )
    if clash is not None:
        raise ConfigError(f"配置项目名称已存在: {name}")
    profile.name = name
    profile.updated_at = datetime.now()
    db.commit()
    db.refresh(profile)
    return profile


def set_default_profile(db: Session, profile_id: int) -> ConfigProfile:
    """将指定项目设为默认,原默认项目取消标记。"""
    profile = db.get(ConfigProfile, profile_id)
    if profile is None:
        raise ConfigError("配置项目不存在")
    previous = (
        db.execute(
            select(ConfigProfile).where(
                ConfigProfile.is_default.is_(True),
                ConfigProfile.id != profile_id,
            )
        )
        .scalars()
        .all()
    )
    for p in previous:
        p.is_default = False
        p.updated_at = datetime.now()
    profile.is_default = True
    profile.updated_at = datetime.now()
    db.commit()
    db.refresh(profile)
    return profile


def delete_profile(db: Session, profile_id: int, *, question_count: int = 0) -> None:
    """删除配置项目。

    Raises:
        ConfigError: 默认项目或仍被题目引用时拒绝删除。
    """
    profile = db.get(ConfigProfile, profile_id)
    if profile is None:
        raise ConfigError("配置项目不存在")
    if profile.is_default:
        raise ConfigError("默认配置项目不可删除,请先设置其他项目为默认")
    if question_count > 0:
        raise ConfigError(f"该配置项目仍被 {question_count} 道题目引用,不可删除")
    db.execute(sa_delete(SystemConfig).where(SystemConfig.profile_id == profile_id))
    db.delete(profile)
    db.commit()
    invalidate_profile_cache(profile_id)


def get_config_dict(
    db: Session, profile_id: int | None = None, *, create_default: bool = True
) -> dict[str, str]:
    """读取指定配置项目的全部配置,返回 ``{key: value}`` 字典。

    Args:
        profile_id: 配置项目 ID;为 None 时使用默认项目
        create_default: 默认项目不存在时是否自动创建(仅首次冷启动需要)
    """
    if profile_id is None:
        profile = get_or_create_default_profile(db) if create_default else None
        if profile is None:
            return {}
        profile_id = profile.id

    now = time.time()
    data = _cache["data"].get(profile_id)
    expires_at = _cache["expires_at"].get(profile_id, 0.0)
    if data is not None and now < expires_at:
        return data

    stmt = select(SystemConfig).where(SystemConfig.profile_id == profile_id)
    rows = db.execute(stmt).scalars().all()
    result = {row.key: row.value for row in rows}

    _cache["data"][profile_id] = result
    _cache["expires_at"][profile_id] = now + _CACHE_TTL
    return result


def upsert_config(
    db: Session, updates: dict, profile_id: int | None = None
) -> dict[str, str]:
    """批量 upsert 指定配置项目的配置项。

    Args:
        db: 数据库 Session
        updates: 待更新的 {key: value} 字典
        profile_id: 配置项目 ID;为 None 时写入默认项目

    Returns:
        更新后的完整配置字典

    Raises:
        ConfigError: 出现未声明的 key
    """
    # 校验所有 key
    unknown = [k for k in updates.keys() if k not in CONFIG_KEYS]
    if unknown:
        raise ConfigError(f"未知配置项: {', '.join(unknown)}")

    if profile_id is None:
        profile_id = get_or_create_default_profile(db).id

    # 查询现有记录
    stmt = select(SystemConfig).where(
        SystemConfig.profile_id == profile_id,
        SystemConfig.key.in_(updates.keys()),
    )
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
            new_row = SystemConfig(
                profile_id=profile_id,
                key=key,
                value=value,
                updated_at=now,
            )
            db.add(new_row)

    db.commit()

    # 失效缓存并返回最新配置
    invalidate_profile_cache(profile_id)
    return get_config_dict(db, profile_id=profile_id)
