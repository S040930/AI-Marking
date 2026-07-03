"""Submission 路由:上传 PDF、列表、详情。

上传成功后通过 ``asyncio.create_task`` 触发批改流水线(OCR → Agent),
在响应返回后于事件循环中执行,列表/详情接口同步返回数据库当前状态。

流水线并发上限:全局 ``pipeline_semaphore`` 控制同时在跑的批改任务数,
闸门满载时 ``POST /submissions`` 返回 ``503 + Retry-After``,由前端提示重试。
"""

import asyncio
import logging
import uuid
from pathlib import Path

import aiofiles
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.core.config import settings
from app.core.time import utc_now_naive
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.schemas.submission import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    ChatRequest,
    ChatResponse,
    ConversationOut,
    FinalizeRequest,
    PaginatedSubmissions,
    SubmissionCreateResponse,
    SubmissionDetail,
    SubmissionStatusOut,
    SuggestionSnapshot,
)
from app.services.agent import AgentError, chat_with_teacher
from app.services.config import get_config_dict
from app.services.marking import run_marking_pipeline
from app.services.queue import (
    has_capacity,
)

router = APIRouter()
logger = logging.getLogger(__name__)

DELETABLE_SUBMISSION_STATUSES = {
    SubmissionStatus.ready_for_review,
    SubmissionStatus.reviewed,
    SubmissionStatus.failed,
}

# 后台批改任务引用保持集合。
# asyncio.create_task 返回的 task 仅被事件循环弱引用,若不显式持有,
# 极端情况下可能被 GC 回收导致任务静默失败。task 完成后通过回调移除引用。
_background_tasks: set[asyncio.Task] = set()


async def _save_pdf(
    upload_file: UploadFile,
    upload_dir: Path,
    suffix: str = "",
    max_size_bytes: int = 50 * 1024 * 1024,
) -> tuple[str, Path]:
    """流式保存上传的 PDF 到 upload_dir。

    Args:
        upload_file: FastAPI 上传文件对象
        upload_dir: 已校验的上传目录
        suffix: 文件名后缀(如 ``_question``),便于运维区分题目与作业 PDF
        max_size_bytes: 单文件最大字节数,超出立刻拒绝(避免内存压力)

    Returns:
        (原始文件名, 服务器保存路径)

    与旧实现的差异:不再 ``await upload_file.read()`` 把整个 PDF 放进内存,
    而是以 1MB chunk 分块写入,期间累计大小,超限抛 413。
    """
    saved_filename = f"{uuid.uuid4().hex}{suffix}.pdf"
    saved_path = upload_dir / saved_filename
    chunk_size = 1024 * 1024  # 1 MB
    written = 0
    try:
        async with aiofiles.open(saved_path, "wb") as out:
            while True:
                chunk = await upload_file.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_size_bytes:
                    saved_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"PDF 文件超过最大允许大小 {max_size_bytes} 字节",
                    )
                await out.write(chunk)
    finally:
        # Starlette 的 UploadFile 需要关闭底层文件,防止连接残留
        await upload_file.close()
    return upload_file.filename or saved_filename, saved_path


@router.post(
    "/submissions",
    response_model=SubmissionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(
    file: UploadFile = File(..., description="学生作业 PDF"),
    question_id: int = Form(..., description="题目库 ID"),
    db: AsyncSession = Depends(get_db),
):
    """上传学生作业 PDF，关联已完成 OCR 的题目并触发批改流程。"""
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="作业文件仅支持 PDF")
    question = await db.get(Question, question_id, with_for_update=True)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if question.status != QuestionStatus.ready or not question.ocr_text:
        raise HTTPException(status_code=409, detail="题目尚未完成 OCR，暂不可用于批改")

    # 解析并校验上传目录(必须位于后端根目录下,防止任意目录写入)
    backend_root = Path(__file__).resolve().parent.parent.parent
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    try:
        upload_dir.relative_to(backend_root)
    except ValueError:
        raise HTTPException(
            status_code=500, detail="UPLOAD_DIR 配置非法,必须位于后端根目录下"
        )
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_filename, saved_path = await _save_pdf(file, upload_dir)

    # 创建记录
    submission = Submission(
        original_filename=original_filename,
        file_path=str(saved_path),
        question_id=question.id,
        status=SubmissionStatus.pending,
    )
    question.last_used_at = utc_now_naive()
    db.add(submission)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        saved_path.unlink(missing_ok=True)
        raise
    await db.refresh(submission)

    # 并发闸门:满载时立即拒绝,避免调度任务被永久挂起
    if not has_capacity():
        # 闸门满载:把刚创建的行直接标 failed,避免 pending 任务永远积压
        submission.status = SubmissionStatus.failed
        submission.error_message = "服务器批改队列繁忙,请稍后重试"
        await db.commit()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "服务器批改队列繁忙,请稍后重试",
                "submission_id": submission.id,
            },
            headers={"Retry-After": "5"},
        )

    # 在事件循环中调度协程任务(BackgroundTasks 不支持 async 函数)
    # ``run_marking_pipeline`` 内部会 ``async with semaphore`` 占用名额
    # task 加入 ``_background_tasks`` 持有强引用,完成时回调移除,避免被 GC 回收
    task = asyncio.create_task(run_marking_pipeline(submission.id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return submission


@router.get("/submissions", response_model=PaginatedSubmissions)
async def list_submissions(
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(10, ge=1, le=100, description="每页记录数"),
    include_count: bool = Query(
        True, description="是否计算 total。轮询场景传 false 跳过 count 查询"
    ),
    db: AsyncSession = Depends(get_db),
):
    """分页列出提交(按上传时间降序)。

    ``include_count=false`` 时跳过 ``COUNT(*)`` 查询,``total`` 返回 ``0``。
    用于前端处理中轮询场景:轮询只需刷新 items,总数由独立的
    ``GET /submissions/count`` 提供,避免每 3s 一次 count 往返。
    """
    base_stmt = (
        select(Submission)
        .options(
            load_only(
                Submission.id,
                Submission.original_filename,
                Submission.question_id,
                Submission.status,
                Submission.score,
                Submission.max_score,
                Submission.confidence,
                Submission.ai_suggestion,
                Submission.uploaded_at,
                Submission.completed_at,
            ),
            selectinload(Submission.question),
        )
        .order_by(Submission.uploaded_at.desc())
    )
    items = (await db.execute(base_stmt.offset(skip).limit(limit))).scalars().all()
    total = (
        (
            await db.execute(select(func.count()).select_from(Submission))
        ).scalar_one()
        if include_count
        else 0
    )
    return PaginatedSubmissions(items=items, total=total, skip=skip, limit=limit)


@router.get("/submissions/count")
async def count_submissions(db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    """返回提交总数。供前端在翻页/首次加载时单独拉取,避免与轮询列表耦合。"""
    total = (
        await db.execute(select(func.count()).select_from(Submission))
    ).scalar_one()
    return {"total": total}


@router.delete(
    "/submissions",
    response_model=BatchDeleteResponse,
    status_code=status.HTTP_200_OK,
)
async def batch_delete_submissions(
    payload: BatchDeleteRequest = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """批量删除提交记录。

    - 仅终态记录允许删除;存在处理中记录时整批原子拒绝
    - 先提交数据库删除,成功后再清理磁盘 PDF(学生作业 + 题目)
    - 关联的 conversations 由 DB CASCADE 自动级联删除
    - 不存在的 ID 安全跳过,不影响其他记录的删除
    - 文件删除失败不阻断流程(如文件已被清理),仅记录日志
    """
    unique_ids = list(dict.fromkeys(payload.ids))
    stmt = (
        select(Submission)
        .where(Submission.id.in_(unique_ids))
        .with_for_update()
    )
    subs = (await db.execute(stmt)).scalars().all()

    blocked_ids = sorted(
        sub.id for sub in subs if sub.status not in DELETABLE_SUBMISSION_STATUSES
    )
    if blocked_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "正在处理的记录不可删除，请等待批改完成后重试",
                "blocked_ids": blocked_ids,
            },
        )

    file_paths = [sub.file_path for sub in subs if sub.file_path]

    for sub in subs:
        await db.delete(sub)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    # 数据库是删除结果的权威来源。提交成功后再清理文件,避免事务失败时
    # 出现“记录仍在但 PDF 已丢失”的不可恢复状态。
    for file_path in file_paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除 submission PDF 失败 [%s]: %s", file_path, exc)

    return BatchDeleteResponse(deleted_count=len(subs))


@router.get("/submissions/{submission_id}", response_model=SubmissionDetail)
async def get_submission(submission_id: int, db: AsyncSession = Depends(get_db)):
    """获取单个提交详情。"""
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    return sub


@router.get(
    "/submissions/{submission_id}/status",
    response_model=SubmissionStatusOut,
)
async def get_submission_status(
    submission_id: int, db: AsyncSession = Depends(get_db)
):
    """仅返回当前提交的处理状态与终态字段。

    用于前端轮询场景:处理中每 2s 拉一次这个轻量接口,进入终态后再拉完整
    ``GET /submissions/{id}``。payload 体积通常小于 200B,远小于完整
    ``SubmissionDetail`` 含 ``ocr_text`` 的几十 KB 至 MB。
    """
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    return SubmissionStatusOut.model_validate(sub)


@router.get("/submissions/{submission_id}/pdf")
async def get_submission_pdf(
    submission_id: int,
    type: str = Query("submission", description="PDF 类型: submission 或 question"),
    db: AsyncSession = Depends(get_db),
):
    """安全返回学生作业或作业题目的 PDF 文件流。

    - 仅终态记录(ready_for_review/reviewed/failed)可访问
    - 不暴露服务器文件路径,仅通过 DB 读取后本地读取
    - 使用浏览器原生 PDF 预览(iframe/object)
    """
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if sub.status not in (
        SubmissionStatus.ready_for_review,
        SubmissionStatus.reviewed,
        SubmissionStatus.failed,
    ):
        raise HTTPException(
            status_code=409,
            detail="作业尚未处理完成,无法预览 PDF",
        )

    if type == "question":
        file_path = sub.question.file_path if sub.question else None
        original_filename = sub.question_original_filename or "question.pdf"
    else:
        file_path = sub.file_path
        original_filename = sub.original_filename or "submission.pdf"

    if not file_path:
        raise HTTPException(status_code=404, detail="PDF 文件不存在")

    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件已过期或被清理")

    return FileResponse(
        path=path,
        media_type="application/pdf",
        filename=original_filename,
        content_disposition_type="inline",
    )


@router.post(
    "/submissions/{submission_id}/chat",
    response_model=ChatResponse,
)
async def chat_with_submission(
    submission_id: int,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """教师就指定作业与 AI 对话。

    - 校验状态为 ready_for_review 或 reviewed
    - 持久化教师消息到 conversations 表
    - 调用 LLM 生成结构化回复(含评分快照与 finalize 意图)
    - 当 AI 判断教师意图为 finalize 时,直接写入最终评分并将状态置为 reviewed
    - 持久化 AI 回复文本到 conversations 表
    """
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if sub.status not in (SubmissionStatus.ready_for_review, SubmissionStatus.reviewed):
        raise HTTPException(
            status_code=409,
            detail="作业尚未准备好进行对话",
        )

    # 拉取历史对话(按 created_at 升序)
    history_stmt = (
        select(Conversation)
        .where(Conversation.submission_id == submission_id)
        .order_by(Conversation.created_at.asc())
    )
    history = (await db.execute(history_stmt)).scalars().all()
    history_dicts = [{"role": h.role, "content": h.content} for h in history]

    # 持久化教师消息
    user_msg = Conversation(
        submission_id=submission_id,
        role="user",
        content=payload.message,
    )
    db.add(user_msg)
    await db.commit()
    await db.refresh(user_msg)

    # 读取 LLM 配置
    config = await get_config_dict(db)

    # 调用 LLM 生成结构化回复
    try:
        chat_result = await chat_with_teacher(
            teacher_message=payload.message,
            ocr_text=sub.ocr_text or "",
            question_text=sub.question_ocr_text or "",
            ai_suggestion=sub.ai_suggestion or {},
            history=history_dicts,
            config=config,
        )
    except AgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 回复失败: {exc}",
        ) from exc

    reply = chat_result["reply"]
    intent = chat_result["intent"]
    suggestion_raw = chat_result.get("suggestion", {})
    reviewer_name = chat_result.get("reviewer_name", "") or (payload.reviewer_name or "")

    # 尝试解析评分快照,失败则降级为空
    try:
        suggestion = SuggestionSnapshot.model_validate(suggestion_raw)
    except Exception:
        suggestion = None

    # 持久化 AI 回复(仅保存文本,结构化评分不进入 conversations 表)
    assistant_msg = Conversation(
        submission_id=submission_id,
        role="assistant",
        content=reply,
    )
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(assistant_msg)

    finalize_payload = None
    if intent == "finalize" and suggestion is not None:
        if sub.status == SubmissionStatus.reviewed:
            # 已审阅状态下不再重复 finalize,意图降级为普通回复
            intent = "reply"
        else:
            # 优先使用教师提供的姓名,其次使用 AI 从消息中提取的姓名
            final_reviewer = payload.reviewer_name or reviewer_name
            if not final_reviewer:
                raise HTTPException(
                    status_code=422,
                    detail="提交最终评分需要提供审核教师姓名",
                )
            try:
                finalize_payload = FinalizeRequest(
                    reviewer_name=final_reviewer,
                    score=suggestion.score,
                    max_score=suggestion.max_score,
                    feedback=suggestion.feedback,
                    details=suggestion.details,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"AI 提取的最终评分格式非法: {exc}",
                ) from exc

            now = utc_now_naive()
            sub.score = finalize_payload.score
            sub.max_score = finalize_payload.max_score
            sub.feedback = finalize_payload.feedback
            sub.details = [item.model_dump() for item in finalize_payload.details]
            sub.reviewed_by = finalize_payload.reviewer_name
            sub.reviewed_at = now
            sub.completed_at = now
            sub.status = SubmissionStatus.reviewed
            await db.commit()
            await db.refresh(sub)

    return ChatResponse(
        reply=reply,
        message_id=assistant_msg.id,
        action=intent,
        suggestion=suggestion,
        finalize_payload=finalize_payload,
    )


@router.get(
    "/submissions/{submission_id}/conversations",
    response_model=list[ConversationOut],
)
async def list_conversations(
    submission_id: int,
    db: AsyncSession = Depends(get_db),
):
    """返回指定作业的教师-AI 对话历史(按时间升序)。"""
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    stmt = (
        select(Conversation)
        .where(Conversation.submission_id == submission_id)
        .order_by(Conversation.created_at.asc())
    )
    items = (await db.execute(stmt)).scalars().all()
    return [ConversationOut.model_validate(item) for item in items]


@router.post(
    "/submissions/{submission_id}/finalize",
    response_model=SubmissionDetail,
)
async def finalize_submission(
    submission_id: int,
    payload: FinalizeRequest,
    db: AsyncSession = Depends(get_db),
):
    """教师确认最终评分。

    - 校验状态为 ready_for_review(已 reviewed 返回 409)
    - 校验分数一致性(由 FinalizeRequest 的 model_validator 处理)
    - 写入 score/max_score/feedback/details/reviewed_by/reviewed_at
    - 状态置为 reviewed
    """
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if sub.status == SubmissionStatus.reviewed:
        raise HTTPException(status_code=409, detail="该作业已审阅,不可重复提交")
    if sub.status != SubmissionStatus.ready_for_review:
        raise HTTPException(
            status_code=409,
            detail="作业尚未准备好进行审阅",
        )

    now = utc_now_naive()
    sub.score = payload.score
    sub.max_score = payload.max_score
    sub.feedback = payload.feedback
    sub.details = [item.model_dump() for item in payload.details]
    sub.reviewed_by = payload.reviewer_name
    sub.reviewed_at = now
    sub.completed_at = now
    sub.status = SubmissionStatus.reviewed
    await db.commit()
    await db.refresh(sub)
    return sub
