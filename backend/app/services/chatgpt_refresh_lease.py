"""ChatGPT 订阅令牌刷新的跨进程租约。

``refresh_token`` 轮换只能消费一次:API 进程与 ``grading_worker`` 进程若同时
刷新,后者的 refresh_token 已被服务端作废,会以 ``invalid_grant`` 失败,整次
批改被误报为"登录已过期"。

这里复用 ``system_config`` 的一行做 claim_token 式租约(TTL 60s),只在
"决定谁去 POST token 端点"的短窗口内持有,**不跨 HTTP 请求持有行锁**:
赢家刷新并落库后才释放,等待方回读新令牌继续。

SQLite(测试/单进程开发)没有跨进程语义,``enforce=None`` 时直接放行。
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.system_config import SystemConfig

CHATGPT_REFRESH_LEASE_KEY = "chatgpt_refresh_lease"
DEFAULT_LEASE_TTL_SECONDS = 60.0


def _parse(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_free(payload: dict[str, Any] | None, *, owner: str, now: float) -> bool:
    if not payload:
        return True
    if payload.get("owner") == owner:
        return True
    try:
        return float(payload.get("expires_at") or 0) <= now
    except (TypeError, ValueError):
        return True


def _payload(owner: str, *, now: float, ttl_seconds: float) -> str:
    return json.dumps(
        {
            "owner": owner,
            "expires_at": now + ttl_seconds,
            "acquired_at": now,
        },
        sort_keys=True,
    )


def _enforced(db: Session, enforce: bool | None) -> bool:
    if enforce is not None:
        return enforce
    return db.get_bind().dialect.name == "postgresql"


def acquire_refresh_lease(
    db: Session,
    *,
    owner: str,
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    enforce: bool | None = None,
) -> bool:
    """尝试取得刷新租约;调用方必须使用**专用 Session**(不含待提交业务改动)。

    返回 True 表示可以发起刷新;False 表示另一个进程正在刷新。
    """
    if not _enforced(db, enforce):
        return True

    now = time.time()
    for _ in range(2):
        row = db.execute(
            select(SystemConfig)
            .where(SystemConfig.key == CHATGPT_REFRESH_LEASE_KEY)
            .with_for_update()
        ).scalar_one_or_none()

        if row is None:
            try:
                db.add(
                    SystemConfig(
                        key=CHATGPT_REFRESH_LEASE_KEY,
                        value=_payload(owner, now=now, ttl_seconds=ttl_seconds),
                    )
                )
                db.commit()
                return True
            except IntegrityError:
                # 另一个进程刚插入同一 key:回滚后走更新分支
                db.rollback()
                continue

        if not _is_free(_parse(row.value), owner=owner, now=now):
            # 立即释放行锁,不能把锁带到 HTTP 刷新期间
            db.rollback()
            return False

        row.value = _payload(owner, now=now, ttl_seconds=ttl_seconds)
        db.commit()
        return True
    return False


def release_refresh_lease(
    db: Session,
    *,
    owner: str,
    enforce: bool | None = None,
) -> bool:
    """仅释放自己的租约;写空串而不是删行,避免与并发插入抢唯一键。"""
    if not _enforced(db, enforce):
        return False

    row = db.execute(
        select(SystemConfig)
        .where(SystemConfig.key == CHATGPT_REFRESH_LEASE_KEY)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        db.rollback()
        return False
    payload = _parse(row.value)
    if payload and payload.get("owner") != owner:
        db.rollback()
        return False
    row.value = ""
    db.commit()
    return True
