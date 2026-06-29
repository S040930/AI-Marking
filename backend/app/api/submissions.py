"""Submission 路由:上传 PDF、列表、详情。

上传成功后通过 ``asyncio.create_task`` 触发批改流水线(OCR → LLM),
在响应返回后于事件循环中执行,列表/详情接口同步返回数据库当前状态。
"""

import asyncio
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.submission import Submission, SubmissionStatus
from app.schemas.submission import (
    PaginatedSubmissions,
    SubmissionCreateResponse,
    SubmissionDetail,
    SubmissionOut,
)
from app.services.marking import run_marking_pipeline

router = APIRouter()


@router.post(
    "/submissions",
    response_model=SubmissionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传 PDF 并触发批改流程。"""
    # 校验 PDF
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

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

    # 保存文件(UUID 命名,保留 .pdf 扩展名)
    saved_filename = f"{uuid.uuid4().hex}.pdf"
    saved_path = upload_dir / saved_filename
    content = await file.read()
    saved_path.write_bytes(content)

    # 创建记录
    submission = Submission(
        original_filename=file.filename or saved_filename,
        file_path=str(saved_path),
        status=SubmissionStatus.pending,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    # 在事件循环中调度协程任务(BackgroundTasks 不支持 async 函数)
    asyncio.create_task(run_marking_pipeline(submission.id))

    return submission


@router.get("/submissions", response_model=PaginatedSubmissions)
def list_submissions(
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(10, ge=1, le=100, description="每页记录数"),
    db: Session = Depends(get_db),
):
    """分页列出提交(按上传时间降序)。"""
    base_stmt = select(Submission).order_by(Submission.uploaded_at.desc())
    items = (
        db.execute(base_stmt.offset(skip).limit(limit)).scalars().all()
    )
    total = db.execute(select(func.count()).select_from(Submission)).scalar_one()
    return PaginatedSubmissions(items=items, total=total, skip=skip, limit=limit)


@router.get("/submissions/{submission_id}", response_model=SubmissionDetail)
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    """获取单个提交详情。"""
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    return sub
