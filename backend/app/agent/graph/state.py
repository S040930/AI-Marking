"""批改图的状态定义与 reducer。

状态只放“图决策所需”的小对象；评分包全文放 ``grading_run_artifacts``，
checkpoint 里只留 ``PackageDigest``（约 2KB）。分支并行写入的字段必须
带 reducer，否则 LangGraph 会以最后一次写入覆盖前一次。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


class PackageDigest(TypedDict, total=False):
    """评分包的结构化摘要；全文在 artifacts 表。"""

    artifact_id: int
    sha256: str
    total_chars: int
    has_code: bool
    grading_revision: int
    rubric_snapshot_id: str | None
    sections: list[str]


class ItemResult(TypedDict, total=False):
    """单个 rubric 项的服务端可校验结果。"""

    rubric_item_id: str
    score: float
    comment: str
    evidence_refs: list[dict[str, Any]]
    evidence_status: Literal["supported", "not_found"]
    confidence: float


class TeacherAnswer(TypedDict, total=False):
    """教师检查点答复（与 grading_runs.teacher_* 镜像）。"""

    verdict: Literal["consistent", "mismatch"]
    note: str | None
    answered_at: str | None
    checkpoint_id: str | None


def merge_item_results(
    left: dict[str, dict] | None, right: dict[str, dict] | None
) -> dict[str, dict]:
    """按 rubric_item_id 合并；同一项后写覆盖，便于重跑修正。"""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


def merge_by_key(
    left: dict[str, dict] | None, right: dict[str, dict] | None
) -> dict[str, dict]:
    """按分支键合并 provider_state / usage_by_agent。"""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


def merge_usage(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any]:
    """跨分支累计 usage；数值相加，非数值保留首个。"""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if isinstance(value, bool):
            merged[key] = value
        elif isinstance(value, (int, float)):
            merged[key] = merged.get(key, 0) + value
        else:
            merged.setdefault(key, value)
    return merged


def merge_counters(
    left: dict[str, int] | None, right: dict[str, int] | None
) -> dict[str, int]:
    """轮次计数器按分支累加；用于 rescore/verify 的循环上限。"""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        merged[key] = merged.get(key, 0) + int(value)
    return merged


class GradingState(TypedDict, total=False):
    """LangGraph 主图状态。

    约定：
    - ``package`` 只存摘要，节点需要正文时通过只读工具按需读取；
    - ``provider_state`` 按分支分槽，避免订阅通道串台；
    - ``errors`` 用 ``operator.add`` 保留所有分支的失败线索。
    """

    run_id: int
    submission_id: int
    question_name: str
    phase: Literal[
        "plan",
        "ingest",
        "rubric",
        "teacher",
        "score",
        "verify",
        "finalize",
        "done",
        "failed",
    ]
    package: PackageDigest
    has_code: bool
    # 主管对话历史（P2/P3）；P4 的子 agent 只保留摘要。
    messages: list[dict]
    rubric_status: Literal["ready", "needs_extraction", "absent"]
    rubric_draft: dict | None
    rubric_items: list[dict]
    # 当前批 fan-out 的任务（每项含 items/brief/attempt）；由 prepare_briefs
    # 或 assemble/rescore 覆盖写入。
    score_tasks: list[dict]
    item_results: Annotated[dict[str, dict], merge_item_results]
    item_reviews: Annotated[dict[str, dict], merge_item_results]
    assessment_draft: dict | None
    verification_errors: list[str]
    teacher: TeacherAnswer | None
    # 注意：``checkpoint_id`` 是 LangGraph 保留 channel 名，不能用作状态字段。
    teacher_checkpoint_id: str | None
    code_test_fingerprint: str
    regenerate_code_tests: bool
    code_test_suite: dict[str, Any]
    code_test_checkpoint_id: str
    code_test_decision: dict[str, Any]
    code_test_action: str
    code_test_results: dict[str, Any]
    provider_state: Annotated[dict[str, dict], merge_by_key]
    usage: Annotated[dict[str, Any], merge_usage]
    usage_by_agent: Annotated[dict[str, dict], merge_by_key]
    counters: Annotated[dict[str, int], merge_counters]
    degraded: bool
    errors: Annotated[list[str], operator.add]
    final: dict | None
