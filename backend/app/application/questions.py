"""题目域用例：改名、切换配置项与安全删除。

从 ``app/api/questions.py`` 迁入的写事务编排；API 层只做参数适配与序列化。
所有写操作沿用既有锁序 ``Question -> Submission(s)``，数据库提交成功后再
清理磁盘文件。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import utc_now_naive
from app.models.question import Question, QuestionReplacementStatus, QuestionStatus
from app.models.submission import Submission
from app.services.document_storage import remove_document_if_unreferenced

logger = logging.getLogger(__name__)


def rename_question(db: Session, question_id: str, name: str) -> Question:
    """重命名题目（非状态机字段，直接写）。"""
    question = db.get(Question, question_id, with_for_update=True)
    if question is None:
        raise NotFoundError("题目不存在")
    if question.replacement_status in (
        QuestionReplacementStatus.pending,
        QuestionReplacementStatus.processing,
    ):
        raise ConflictError("题目新版正在处理中")
    question.name = name.strip()
    question.updated_at = utc_now_naive()
    db.commit()
    return question


def change_question_config_profile(
    db: Session, question_id: str, config_profile_id: int
) -> Question:
    """切换题目使用的配置项目（非状态机字段，直接写）。"""
    question = db.get(Question, question_id, with_for_update=True)
    if question is None:
        raise NotFoundError("题目不存在")
    if question.status in (QuestionStatus.pending, QuestionStatus.ocr_processing):
        raise ConflictError("题目 OCR 正在处理中")
    question.config_profile_id = config_profile_id
    question.updated_at = utc_now_naive()
    db.commit()
    return question


def _locked_submissions(db: Session, question_id: str) -> list[Submission]:
    return (
        db.execute(
            select(Submission)
            .options(
                selectinload(Submission.code_files),
                selectinload(Submission.code_input_files),
            )
            .where(Submission.question_id == question_id)
            .order_by(Submission.id)
            .with_for_update()
        )
        .scalars()
        .all()
    )


def delete_question(
    db: Session, question_id: str, confirmation_name: str
) -> int:
    """删除题目与其全部关联作业，返回删除的作业数。

    - 仅允许终态作业；存在处理中记录时原子拒绝并携带 blocked ID 列表
    - 先提交数据库删除，成功后再清理磁盘 PDF 与代码产物目录
    """
    question = db.get(Question, question_id, with_for_update=True)
    if question is None:
        raise NotFoundError("题目不存在")
    if confirmation_name != question.name:
        raise ValidationError("题目名称确认不匹配")
    if question.status in (QuestionStatus.pending, QuestionStatus.ocr_processing):
        raise ConflictError("题目 OCR 正在处理中")
    if question.replacement_status in (
        QuestionReplacementStatus.pending,
        QuestionReplacementStatus.processing,
    ):
        raise ConflictError("题目新版正在处理中")

    from app.application.uploads import DELETABLE_SUBMISSION_STATUSES

    submissions = _locked_submissions(db, question_id)
    blocked = sorted(
        sub.id
        for sub in submissions
        if sub.status not in DELETABLE_SUBMISSION_STATUSES
    )
    if blocked:
        raise ConflictError(
            {
                "message": "存在正在处理的批改记录，无法删除题目",
                "blocked_submission_ids": blocked,
            }
        )

    paths = [question.file_path]
    if question.replacement_file_path:
        paths.append(question.replacement_file_path)
    paths.extend(sub.file_path for sub in submissions if sub.file_path)
    paths.extend(
        code_file.file_path
        for sub in submissions
        for code_file in sub.code_files
        if code_file.file_path
    )
    paths.extend(
        input_file.file_path
        for sub in submissions
        for input_file in sub.code_input_files
        if input_file.file_path
    )
    artifact_roots = [
        Path(settings.UPLOAD_DIR).resolve() / "code-artifacts" / str(sub.id)
        for sub in submissions
    ]

    for sub in submissions:
        db.delete(sub)
    db.flush()
    db.delete(question)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    _unlink_after_commit(db, paths)
    for artifact_root in artifact_roots:
        shutil.rmtree(artifact_root, ignore_errors=True)
    return len(submissions)


def _unlink_after_commit(db: Session, paths: list[str]) -> None:
    for file_path in paths:
        try:
            remove_document_if_unreferenced(db, file_path, Path(settings.UPLOAD_DIR))
        except (OSError, ValueError) as exc:
            logger.warning("清理题目库关联 PDF 失败 [%s]: %s", file_path, exc)
