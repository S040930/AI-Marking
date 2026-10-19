"""GradingRun 批改域状态与策略:无框架依赖。

run 状态机独立于 ``SubmissionStatus``:
- ``queued``/``starting``/``running`` 描述 worker 侧生命周期;
- ``waiting_for_teacher`` 描述教师检查点驻留;
- 终态为 ``completed``/``failed``/``cancelled``。
"""

from __future__ import annotations

import enum
from collections.abc import Mapping


class GradingRunStatus(str, enum.Enum):
    queued = "queued"
    starting = "starting"
    running = "running"
    waiting_for_teacher = "waiting_for_teacher"
    cancelling = "cancelling"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_RUN_STATUSES = frozenset(
    {
        GradingRunStatus.completed,
        GradingRunStatus.failed,
        GradingRunStatus.cancelled,
    }
)

GRADING_RUN_TRANSITIONS: Mapping[GradingRunStatus, frozenset[GradingRunStatus]] = {
    GradingRunStatus.queued: frozenset(
        {
            GradingRunStatus.starting,
            GradingRunStatus.cancelled,
            GradingRunStatus.cancelling,
        }
    ),
    GradingRunStatus.starting: frozenset(
        {
            GradingRunStatus.running,
            GradingRunStatus.waiting_for_teacher,
            GradingRunStatus.completed,
            GradingRunStatus.failed,
            GradingRunStatus.cancelled,
            GradingRunStatus.cancelling,
        }
    ),
    GradingRunStatus.running: frozenset(
        {
            GradingRunStatus.waiting_for_teacher,
            GradingRunStatus.completed,
            GradingRunStatus.failed,
            GradingRunStatus.cancelled,
            GradingRunStatus.cancelling,
        }
    ),
    GradingRunStatus.waiting_for_teacher: frozenset(
        {
            # 教师答复后续跑:回到 running;也可直接取消
            GradingRunStatus.running,
            GradingRunStatus.queued,
            GradingRunStatus.completed,
            GradingRunStatus.failed,
            GradingRunStatus.cancelled,
            GradingRunStatus.cancelling,
        }
    ),
    GradingRunStatus.completed: frozenset(),
    GradingRunStatus.failed: frozenset(),
    GradingRunStatus.cancelling: frozenset({GradingRunStatus.cancelled}),
    GradingRunStatus.cancelled: frozenset(),
}


def ensure_run_transition(current: GradingRunStatus, target: GradingRunStatus) -> None:
    allowed = GRADING_RUN_TRANSITIONS[current]
    if target not in allowed:
        raise ValueError(f"批改运行状态不可从 {current.value} 迁移到 {target.value}")


class CheckpointVerdict(str, enum.Enum):
    """教师检查点答复。"""

    consistent = "consistent"
    mismatch = "mismatch"


class RunProvider(str, enum.Enum):
    """模型通道。"""

    openai_api = "openai_api"
    chatgpt_sub = "chatgpt_sub"
