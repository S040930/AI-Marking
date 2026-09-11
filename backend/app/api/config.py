"""系统配置路由:全局配置的读取/更新。

MVP 内部工具,不做鉴权;敏感字段(API Key/Secret)返回真实值供前端编辑回填,
前端通过 ``Input.Password`` 隐藏显示。

系统仅维护一份全局配置,``GET/PUT /api/config`` 直接操作这一份。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.system_config import ConfigOut, ConfigUpdate
from app.services.config import ConfigError, get_config_dict, upsert_config
from app.services.rubric import normalize_definition

router = APIRouter()


def _to_config_out(config: dict[str, str]) -> ConfigOut:
    """将配置字典转为 ConfigOut,缺失字段填空字符串。"""
    rubric_definition = None
    if config.get("rubric_definition"):
        try:
            rubric_definition = normalize_definition(json.loads(config["rubric_definition"])).model_dump(exclude_none=True)
        except (TypeError, ValueError):
            rubric_definition = None
    return ConfigOut(
        paddleocr_api_url=config.get("paddleocr_api_url", "") or "",
        paddleocr_token=config.get("paddleocr_token", "") or "",
        rubric_definition=rubric_definition,
        review_enabled=(config.get("review_enabled", "true") or "true").lower()
        == "true",
    )


@router.get("/config", response_model=ConfigOut)
def get_config(db: Session = Depends(get_db)):
    """返回全部配置(缺失字段为空字符串)。"""
    config = get_config_dict(db)
    return _to_config_out(config)


@router.put("/config", response_model=ConfigOut)
def update_config(
    payload: ConfigUpdate,
    db: Session = Depends(get_db),
):
    """更新配置(子集 upsert)。

    - 未提供的字段保持不变
    - 字段值为空字符串表示清空
    - 未知 key 返回 400
    - 返回更新后的完整配置
    """
    # 只取显式提供的字段(None 表示未提供,不更新)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("rubric_definition", object()) is None:
        updates["rubric_definition"] = ""
    if "rubric_definition" in updates and updates["rubric_definition"]:
        try:
            updates["rubric_definition"] = normalize_definition(updates["rubric_definition"]).model_dump(exclude_none=True)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"结构化 rubric 无效: {exc}") from exc

    if not updates:
        # 无更新,直接返回当前配置
        return _to_config_out(get_config_dict(db))

    try:
        new_config = upsert_config(db, updates)
    except ConfigError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return _to_config_out(new_config)
