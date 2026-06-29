"""系统配置路由:读取与更新 LLM/OCR/Rubric 配置项。

MVP 内部工具,不做鉴权;敏感字段(API Key/Secret)返回真实值供前端编辑回填,
前端通过 ``Input.Password`` 隐藏显示。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.system_config import ConfigOut, ConfigUpdate
from app.services.config import ConfigError, get_config_dict, upsert_config

router = APIRouter()


def _to_config_out(config: dict[str, str]) -> ConfigOut:
    """将配置字典转为 ConfigOut,缺失字段填空字符串。"""
    return ConfigOut(
        llm_api_key=config.get("llm_api_key", "") or "",
        llm_base_url=config.get("llm_base_url", "") or "",
        llm_model=config.get("llm_model", "") or "",
        paddleocr_api_url=config.get("paddleocr_api_url", "") or "",
        paddleocr_token=config.get("paddleocr_token", "") or "",
        rubric=config.get("rubric", "") or "",
        llm_user_prompt=config.get("llm_user_prompt", "") or "",
    )


@router.get("/config", response_model=ConfigOut)
def get_config(db: Session = Depends(get_db)):
    """返回当前所有配置(缺失字段为空字符串)。"""
    config = get_config_dict(db)
    return _to_config_out(config)


@router.put("/config", response_model=ConfigOut)
def update_config(payload: ConfigUpdate, db: Session = Depends(get_db)):
    """批量更新配置(子集 upsert)。

    - 未提供的字段保持不变
    - 字段值为空字符串表示清空
    - 未知 key 返回 400
    - 返回更新后的完整配置
    """
    # 只取显式提供的字段(None 表示未提供,不更新)
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}

    if not updates:
        # 无更新,直接返回当前配置
        return _to_config_out(get_config_dict(db))

    try:
        new_config = upsert_config(db, updates)
    except ConfigError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return _to_config_out(new_config)
