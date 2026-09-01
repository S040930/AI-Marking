"""Upload/retry/replace use cases with short transactions and fixed lock order.

All public functions own their sessions. File streaming happens between a read-only
preflight call and a committing call, so no database transaction spans an upload.
The committing calls lock ``Question -> Submission(s) -> BackgroundJob``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.application.lifecycle import (
    transition_question,
    transition_question_replacement,
    transition_submission,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import utc_now_naive
from app.models.config_profile import ConfigProfile
from app.models.question import Question, QuestionReplacementStatus, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.models.submission_code_file import SubmissionCodeFile
from app.services.config import DEFAULT_PROFILE_NAME
from app.services.document_storage import StoredDocument
from app.services.events import notify_question_status, notify_submission_status
from app.services.queue import (
    new_question_ocr_job,
    new_submission_ocr_job,
    reset_question_ocr_job,
    reset_question_replace_job,
    reset_submission_ocr_job,
)

DELETABLE_SUBMISSION_STATUSES = frozenset(
    {
        SubmissionStatus.awaiting_mcp,
        SubmissionStatus.ready_for_review,
        SubmissionStatus.reviewed,
        SubmissionStatus.failed,
    }
)


@dataclass(frozen=True, slots=True)
class SubmissionPreflight:
    question_id: str
    question_text: str


@dataclass(frozen=True, slots=True)
class RetryPreflight:
    question_id: str
    original_filename: str
    old_path: str


@dataclass(frozen=True, slots=True)
class ReplacePreflight:
    affected_submission_count: int


def _ready_question(question: Question | None, *, retry: bool = False) -> Question:
    if question is None:
        raise NotFoundError("题目不存在")
    if question.status != QuestionStatus.ready or not question.ocr_text:
        message = "关联题目当前不可用于批改" if retry else "题目尚未完成 OCR，暂不可用于批改"
        raise ConflictError(message)
    if question.replacement_status in (
        QuestionReplacementStatus.pending,
        QuestionReplacementStatus.processing,
    ):
        raise ConflictError("题目新版正在处理中，暂不可重试" if retry else "题目新版正在处理中，暂不可用于批改")
    return question


def preflight_submission_create(
    factory: sessionmaker[Session], question_id: str
) -> SubmissionPreflight:
    with factory() as db:
        question = _ready_question(db.get(Question, question_id))
        return SubmissionPreflight(question.id, question.ocr_text or "")


def commit_submission_create(
    factory: sessionmaker[Session],
    *,
    question_id: str,
    stored: StoredDocument,
    code_metadata: list[dict],
) -> Submission:
    with factory() as db:
        question = _ready_question(
            db.get(Question, question_id, with_for_update=True)
        )
        submission = Submission(
            original_filename=stored.original_filename,
            file_path=str(stored.path),
            file_sha256=stored.sha256,
            question_id=question.id,
            status=SubmissionStatus.pending,
        )
        question.last_used_at = utc_now_naive()
        db.add(submission)
        db.flush()
        for metadata in code_metadata:
            db.add(
                SubmissionCodeFile(
                    submission_id=submission.id,
                    question_number=metadata["question_number"],
                    entrypoint=metadata.get("entrypoint", True),
                    original_filename=metadata["filename"],
                    file_path=metadata["path"],
                    file_kind=metadata["kind"],
                    source_sha256=metadata["source_sha256"],
                    source_text=metadata["source_text"],
                )
            )
        db.add(new_submission_ocr_job(submission.id))
        db.commit()
        db.refresh(submission)
        return submission


def preflight_submission_retry(
    factory: sessionmaker[Session], submission_id: int, *, replacing_file: bool
) -> RetryPreflight:
    with factory() as db:
        sub = db.scalar(
            select(Submission)
            .where(Submission.id == submission_id)
            .options(
                selectinload(Submission.code_files),
                selectinload(Submission.code_input_files),
            )
        )
        if sub is None:
            raise NotFoundError("提交记录不存在")
        if sub.status != SubmissionStatus.failed:
            raise ConflictError("仅失败的作业可以重新批改")
        _ready_question(db.get(Question, sub.question_id), retry=True)
        if not replacing_file and not Path(sub.file_path).is_file():
            raise ConflictError("原学生作业文件已过期，请重新选择 PDF 后重试")
        if any(not Path(item.file_path).is_file() for item in sub.code_files):
            raise ConflictError("原代码文件已过期，请通过编程助手（MCP）重新提交 PDF 与全部代码文件")
        if any(not Path(item.file_path).is_file() for item in sub.code_input_files):
            raise ConflictError("原数据集文件已过期，请通过编程助手（MCP）重新提交 PDF、代码与数据集")
        return RetryPreflight(sub.question_id, sub.original_filename, sub.file_path)


def commit_submission_retry(
    factory: sessionmaker[Session],
    *,
    submission_id: int,
    question_id: str,
    stored: StoredDocument | None,
) -> tuple[Submission, str]:
    with factory() as db:
        _ready_question(db.get(Question, question_id, with_for_update=True), retry=True)
        sub = db.scalar(
            select(Submission)
            .where(Submission.id == submission_id)
            .options(
                selectinload(Submission.code_files),
                selectinload(Submission.code_input_files),
            )
            .with_for_update()
        )
        if sub is None:
            raise NotFoundError("提交记录不存在")
        if sub.question_id != question_id or sub.status != SubmissionStatus.failed:
            raise ConflictError("作业状态已变化，请刷新后重试")
        old_path = sub.file_path
        if stored is not None:
            sub.file_path = str(stored.path)
            sub.file_sha256 = stored.sha256
            sub.original_filename = stored.original_filename
        elif not Path(sub.file_path).is_file():
            raise ConflictError("原学生作业文件已过期，请重新选择 PDF 后重试")
        for field in (
            "ocr_text", "score", "max_score", "confidence", "feedback", "details",
            "assessment_suggestion", "assessment_review", "reviewed_by", "reviewed_at",
            "completed_at", "error_message", "graded_at",
        ):
            setattr(sub, field, None)
        for code_file in sub.code_files:
            code_file.execution_status = "pending"
            code_file.execution_result = None
            code_file.artifacts = None
            code_file.visual_reviews = None
        sub.grading_revision = 0
        transition_submission(sub, SubmissionStatus.pending)
        notify_submission_status(db, submission_id, sub.status.value)
        reset_submission_ocr_job(db, submission_id)
        db.commit()
        db.refresh(sub)
        return sub, old_path


def preflight_question_create(
    factory: sessionmaker[Session], question_id: str, config_profile_id: int | None
) -> int:
    with factory() as db:
        if db.get(Question, question_id) is not None:
            raise ConflictError("同名题目已存在，请修改文件名后重新上传")
        if config_profile_id is not None:
            if db.get(ConfigProfile, config_profile_id) is None:
                raise ValidationError("配置项目不存在")
            return config_profile_id
        profile = db.scalar(
            select(ConfigProfile).where(ConfigProfile.is_default.is_(True))
        )
        if profile is None:
            profile = ConfigProfile(name=DEFAULT_PROFILE_NAME, is_default=True)
            db.add(profile)
            db.commit()
            db.refresh(profile)
        return profile.id


def commit_question_create(
    factory: sessionmaker[Session],
    *,
    question_id: str,
    name: str,
    config_profile_id: int,
    stored: StoredDocument,
) -> Question:
    with factory() as db:
        if db.get(Question, question_id, with_for_update=True) is not None:
            raise ConflictError("同名题目已存在，请修改文件名后重新上传")
        if db.get(ConfigProfile, config_profile_id) is None:
            raise ValidationError("配置项目不存在")
        question = Question(
            id=question_id,
            name=name,
            original_filename=stored.original_filename,
            file_path=str(stored.path),
            file_sha256=stored.sha256,
            status=QuestionStatus.pending,
            config_profile_id=config_profile_id,
        )
        db.add(question)
        try:
            db.flush()
            db.add(new_question_ocr_job(question.id))
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError("同名题目已存在，请修改文件名后重新上传") from exc
        db.refresh(question)
        return question


def preflight_question_retry(factory: sessionmaker[Session], question_id: str) -> None:
    with factory() as db:
        question = db.get(Question, question_id)
        if question is None:
            raise NotFoundError("题目不存在")
        if question.status != QuestionStatus.failed:
            raise ConflictError("仅失败的题目可以重试 OCR")


def commit_question_retry(
    factory: sessionmaker[Session], question_id: str, stored: StoredDocument | None
) -> tuple[Question, str, int]:
    with factory() as db:
        question = db.get(Question, question_id, with_for_update=True)
        if question is None:
            raise NotFoundError("题目不存在")
        if question.status != QuestionStatus.failed:
            raise ConflictError("题目状态已变化，请刷新后重试")
        old_path = question.file_path
        if stored is not None:
            question.file_path = str(stored.path)
            question.file_sha256 = stored.sha256
            question.original_filename = stored.original_filename
        elif not Path(question.file_path).is_file():
            raise ConflictError("原题目文件已过期，请重新选择 PDF 后重试")
        transition_question(question, QuestionStatus.pending)
        question.error_message = None
        question.updated_at = utc_now_naive()
        notify_question_status(db, question.id, question.status.value, None)
        reset_question_ocr_job(db, question.id)
        submission_count = db.scalar(
            select(func.count(Submission.id)).where(
                Submission.question_id == question.id
            )
        ) or 0
        db.commit()
        db.refresh(question)
        return question, old_path, submission_count


def preflight_question_replace(
    factory: sessionmaker[Session],
    question_id: str,
    confirmation_name: str,
    *,
    acknowledge_deletion: bool,
) -> ReplacePreflight:
    with factory() as db:
        question = db.get(Question, question_id)
        if question is None:
            raise NotFoundError("题目不存在")
        if confirmation_name != question.name:
            raise ValidationError("题目名称确认不匹配")
        if question.status != QuestionStatus.ready or not question.ocr_text:
            raise ConflictError("只有可使用的题目可以上传新版")
        if question.replacement_status in (
            QuestionReplacementStatus.pending,
            QuestionReplacementStatus.processing,
        ):
            raise ConflictError("题目新版正在处理中")
        submissions = db.scalars(
            select(Submission).where(Submission.question_id == question_id)
        ).all()
        blocked = [sub.id for sub in submissions if sub.status not in DELETABLE_SUBMISSION_STATUSES]
        if blocked:
            raise ConflictError(
                {
                    "message": "存在正在处理的批改记录，无法更新题目",
                    "blocked_submission_ids": sorted(blocked),
                }
            )
        if submissions and not acknowledge_deletion:
            raise ValidationError(
                {
                    "message": "替换题目将永久删除关联的历史批改记录,需二次确认",
                    "affected_submission_count": len(submissions),
                    "acknowledge_required": True,
                }
            )
        return ReplacePreflight(len(submissions))


def commit_question_replace(
    factory: sessionmaker[Session],
    *,
    question_id: str,
    confirmation_name: str,
    acknowledge_deletion: bool,
    stored: StoredDocument,
) -> tuple[Question, int, str | None]:
    with factory() as db:
        question = db.get(Question, question_id, with_for_update=True)
        if question is None:
            raise NotFoundError("题目不存在")
        if confirmation_name != question.name:
            raise ValidationError("题目名称确认不匹配")
        if question.status != QuestionStatus.ready or not question.ocr_text:
            raise ConflictError("只有可使用的题目可以上传新版")
        if question.replacement_status in (
            QuestionReplacementStatus.pending,
            QuestionReplacementStatus.processing,
        ):
            raise ConflictError("题目新版正在处理中")
        submissions = db.scalars(
            select(Submission)
            .where(Submission.question_id == question_id)
            .order_by(Submission.id)
            .with_for_update()
        ).all()
        blocked = [sub.id for sub in submissions if sub.status not in DELETABLE_SUBMISSION_STATUSES]
        if blocked:
            raise ConflictError(
                {
                    "message": "存在正在处理的批改记录，无法更新题目",
                    "blocked_submission_ids": sorted(blocked),
                }
            )
        if submissions and not acknowledge_deletion:
            raise ValidationError(
                {
                    "message": "替换题目将永久删除关联的历史批改记录,需二次确认",
                    "affected_submission_count": len(submissions),
                    "acknowledge_required": True,
                }
            )
        previous_staged = question.replacement_file_path
        transition_question_replacement(question, QuestionReplacementStatus.pending)
        question.replacement_file_path = str(stored.path)
        question.replacement_file_sha256 = stored.sha256
        question.replacement_original_filename = stored.original_filename
        question.replacement_error_message = None
        question.updated_at = utc_now_naive()
        notify_question_status(
            db, question.id, question.status.value, question.replacement_status.value
        )
        reset_question_replace_job(db, question.id)
        db.commit()
        db.refresh(question)
        return question, len(submissions), previous_staged
