"""MCP 客户端预检计划（prepared plan）的进程内生命周期管理。"""

from __future__ import annotations

import secrets
import time

from app.mcp.errors import McpApiError

PLAN_TTL_SECONDS = 30 * 60
MAX_PREPARED_PLANS = 256
_prepared_plans: dict[str, dict] = {}


def make_plan(plan: dict) -> str:
    purge_expired_plans()
    if len(_prepared_plans) >= MAX_PREPARED_PLANS:
        raise McpApiError("待提交预检计划已达到上限，请先提交现有计划或稍后重试")
    plan_id = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + PLAN_TTL_SECONDS
    _prepared_plans[plan_id] = {**plan, "expires_at": expires_at}
    return plan_id


def purge_expired_plans() -> None:
    now = int(time.time())
    for key, plan in list(_prepared_plans.items()):
        if plan.get("expires_at", 0) < now:
            _prepared_plans.pop(key, None)


def read_plan(token: str) -> dict:
    purge_expired_plans()
    plan_id = token
    plan = _prepared_plans.get(plan_id)
    if not plan:
        raise McpApiError("submission_plan 无效或 MCP 已重启，请重新预检")
    return plan


def discard_plan(token: str) -> None:
    """提交成功后移除对应预检计划（token 含点号时取点号前部分，兼容历史格式）。"""
    _prepared_plans.pop(token.split(".", 1)[0], None)
