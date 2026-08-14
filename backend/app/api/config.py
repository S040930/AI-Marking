"""系统配置路由:配置项目的管理与每个项目内配置的读取/更新。

MVP 内部工具,不做鉴权;敏感字段(API Key/Secret)返回真实值供前端编辑回填,
前端通过 ``Input.Password`` 隐藏显示。

配置项目(profile)是独立的一套 LLM/OCR/Rubric/提示词配置,题目通过
``Question.config_profile_id`` 绑定使用哪一套。``GET/PUT /api/config``
默认操作默认配置项目,也可用 ``?profile_id=`` 指定。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.question import Question
from app.schemas.system_config import (
    ConfigOut,
    ConfigProfileCreate,
    ConfigProfileOut,
    ConfigProfileRename,
    ConfigUpdate,
)
from app.services.agent import close_llm_clients_sync
from app.services.config import (
    ConfigError,
    create_profile,
    delete_profile,
    get_config_dict,
    get_profile,
    list_profiles,
    rename_profile,
    set_default_profile,
    upsert_config,
)
from app.services.rubric import normalize_definition

router = APIRouter()


REVIEW_LLM_KEYS = ("review_llm_api_key", "review_llm_base_url", "review_llm_model")

# LLM 相关配置 key,任一变更后需清空客户端缓存以避免陈旧连接复用
_LLM_CONFIG_KEYS = (
    "llm_api_key",
    "llm_base_url",
    "llm_model",
    "review_llm_api_key",
    "review_llm_base_url",
    "review_llm_model",
)


def _to_config_out(config: dict[str, str]) -> ConfigOut:
    """将配置字典转为 ConfigOut,缺失字段填空字符串。"""
    rubric_definition = None
    if config.get("rubric_definition"):
        try:
            rubric_definition = normalize_definition(json.loads(config["rubric_definition"])).model_dump(exclude_none=True)
        except (TypeError, ValueError):
            rubric_definition = None
    return ConfigOut(
        llm_api_key=config.get("llm_api_key", "") or "",
        llm_base_url=config.get("llm_base_url", "") or "",
        llm_model=config.get("llm_model", "") or "",
        review_llm_api_key=config.get("review_llm_api_key", "") or "",
        review_llm_base_url=config.get("review_llm_base_url", "") or "",
        review_llm_model=config.get("review_llm_model", "") or "",
        paddleocr_api_url=config.get("paddleocr_api_url", "") or "",
        paddleocr_token=config.get("paddleocr_token", "") or "",
        rubric_definition=rubric_definition,
        llm_user_prompt=config.get("llm_user_prompt", "") or "",
    )


def _profile_or_404(db: Session, profile_id: int) -> None:
    if get_profile(db, profile_id) is None:
        raise HTTPException(status_code=404, detail="配置项目不存在")


def _question_count(db: Session, profile_id: int) -> int:
    return (
        db.execute(
            select(func.count(Question.id)).where(
                Question.config_profile_id == profile_id
            )
        )
    ).scalar_one()


@router.get("/config", response_model=ConfigOut)
def get_config(
    profile_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """返回指定配置项目的全部配置(缺省为默认项目,缺失字段为空字符串)。"""
    config = get_config_dict(db, profile_id=profile_id)
    return _to_config_out(config)


@router.put("/config", response_model=ConfigOut)
def update_config(
    payload: ConfigUpdate,
    profile_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """更新指定配置项目(子集 upsert)。

    - 未提供的字段保持不变
    - 字段值为空字符串表示清空
    - 未知 key 返回 400
    - 返回更新后的完整配置
    """
    if profile_id is not None:
        _profile_or_404(db, profile_id)

    # 只取显式提供的字段(None 表示未提供,不更新)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("rubric_definition", object()) is None:
        updates["rubric_definition"] = ""
    if "rubric_definition" in updates and updates["rubric_definition"]:
        try:
            updates["rubric_definition"] = normalize_definition(updates["rubric_definition"]).model_dump(exclude_none=True)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"结构化 rubric 无效: {exc}") from exc

    # 审核 LLM「全有或全无」校验:若提交了任一 review_llm_* 字段,
    # 则三字段在合并现有值后必须同时非空或同时为空
    if any(k in updates for k in REVIEW_LLM_KEYS):
        current = get_config_dict(db, profile_id=profile_id)
        merged = {**{k: current.get(k, "") for k in REVIEW_LLM_KEYS}, **updates}
        filled = [k for k in REVIEW_LLM_KEYS if merged[k]]
        if 0 < len(filled) < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="审核 LLM 的 API Key、Base URL、Model 必须同时填写或同时留空",
            )

    if not updates:
        # 无更新,直接返回当前配置
        return _to_config_out(get_config_dict(db, profile_id=profile_id))

    try:
        new_config = upsert_config(db, updates, profile_id=profile_id)
    except ConfigError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # LLM 相关配置变更后清空客户端缓存,下次调用重建连接
    if any(k in updates for k in _LLM_CONFIG_KEYS):
        close_llm_clients_sync()

    return _to_config_out(new_config)


# ---------------- 配置项目 CRUD ----------------


@router.get("/config/profiles", response_model=list[ConfigProfileOut])
def get_profiles(db: Session = Depends(get_db)):
    """列出全部配置项目。"""
    return list_profiles(db)


@router.post("/config/profiles", response_model=ConfigProfileOut, status_code=201)
def post_profile(payload: ConfigProfileCreate, db: Session = Depends(get_db)):
    """新建配置项目,可选从现有项目复制配置值。"""
    try:
        return create_profile(db, payload.name, copy_from_id=payload.copy_from_id)
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/config/profiles/{profile_id}", response_model=ConfigProfileOut)
def patch_profile(
    profile_id: int,
    payload: ConfigProfileRename,
    db: Session = Depends(get_db),
):
    """重命名配置项目。"""
    if get_profile(db, profile_id) is None:
        raise HTTPException(status_code=404, detail="配置项目不存在")
    try:
        return rename_profile(db, profile_id, payload.name)
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/config/profiles/{profile_id}/default",
    response_model=ConfigProfileOut,
)
def post_profile_default(profile_id: int, db: Session = Depends(get_db)):
    """将指定项目设为默认(新题目未指定时回退)。"""
    if get_profile(db, profile_id) is None:
        raise HTTPException(status_code=404, detail="配置项目不存在")
    try:
        return set_default_profile(db, profile_id)
    except ConfigError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/config/profiles/{profile_id}", status_code=204)
def delete_profile_route(profile_id: int, db: Session = Depends(get_db)):
    """删除配置项目(默认项目或被题目引用时拒绝)。"""
    if get_profile(db, profile_id) is None:
        raise HTTPException(status_code=404, detail="配置项目不存在")
    try:
        delete_profile(db, profile_id, question_count=_question_count(db, profile_id))
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
