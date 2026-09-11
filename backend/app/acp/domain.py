"""ACP 批改域状态与策略:无框架依赖。

run 状态机独立于 ``SubmissionStatus``:
- ``queued``/``starting``/``running`` 描述 worker 侧生命周期;
- ``waiting_for_teacher`` 描述教师检查点驻留;
- 终态为 ``completed``/``failed``/``cancelled``。
"""

from __future__ import annotations

import enum
from collections.abc import Mapping


class AcpRunStatus(str, enum.Enum):
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
        AcpRunStatus.completed,
        AcpRunStatus.failed,
        AcpRunStatus.cancelled,
    }
)

ACP_RUN_TRANSITIONS: Mapping[AcpRunStatus, frozenset[AcpRunStatus]] = {
    AcpRunStatus.queued: frozenset(
        {
            AcpRunStatus.starting,
            AcpRunStatus.cancelled,
            AcpRunStatus.cancelling,
        }
    ),
    AcpRunStatus.starting: frozenset(
        {
            AcpRunStatus.running,
            AcpRunStatus.waiting_for_teacher,
            AcpRunStatus.completed,
            AcpRunStatus.failed,
            AcpRunStatus.cancelled,
            AcpRunStatus.cancelling,
        }
    ),
    AcpRunStatus.running: frozenset(
        {
            AcpRunStatus.waiting_for_teacher,
            AcpRunStatus.completed,
            AcpRunStatus.failed,
            AcpRunStatus.cancelled,
            AcpRunStatus.cancelling,
        }
    ),
    AcpRunStatus.waiting_for_teacher: frozenset(
        {
            # 教师答复后续跑:回到 running;也可直接取消
            AcpRunStatus.running,
            AcpRunStatus.queued,
            AcpRunStatus.completed,
            AcpRunStatus.failed,
            AcpRunStatus.cancelled,
            AcpRunStatus.cancelling,
        }
    ),
    AcpRunStatus.completed: frozenset(),
    AcpRunStatus.failed: frozenset(),
    AcpRunStatus.cancelling: frozenset({AcpRunStatus.cancelled}),
    AcpRunStatus.cancelled: frozenset(),
}


def ensure_run_transition(current: AcpRunStatus, target: AcpRunStatus) -> None:
    allowed = ACP_RUN_TRANSITIONS[current]
    if target not in allowed:
        raise ValueError(f"ACP run 状态不可从 {current.value} 迁移到 {target.value}")


class AcpChatStatus(str, enum.Enum):
    """交互式对话会话状态。

    - ``idle``:等待教师下一条消息(进程可驻留也可待惰性拉起);
    - ``running``:一个 agentic turn 进行中;
    - ``waiting_permission``:agent 请求权限,等待教师批准/拒绝;
    - ``closed``:会话结束(教师关闭或进程退出后落库);
    - ``error``:不可恢复错误,事件流保留供回看。
    """

    idle = "idle"
    running = "running"
    waiting_permission = "waiting_permission"
    closed = "closed"
    error = "error"


TERMINAL_CHAT_STATUSES = frozenset(
    {AcpChatStatus.closed, AcpChatStatus.error}
)

ACP_CHAT_TRANSITIONS: Mapping[AcpChatStatus, frozenset[AcpChatStatus]] = {
    AcpChatStatus.idle: frozenset(
        {AcpChatStatus.running, AcpChatStatus.closed, AcpChatStatus.error}
    ),
    AcpChatStatus.running: frozenset(
        {
            AcpChatStatus.waiting_permission,
            AcpChatStatus.idle,
            AcpChatStatus.closed,
            AcpChatStatus.error,
        }
    ),
    # 权限裁决或超时后回到 running 续行;关闭/错误直接终态
    AcpChatStatus.waiting_permission: frozenset(
        {AcpChatStatus.running, AcpChatStatus.idle, AcpChatStatus.closed, AcpChatStatus.error}
    ),
    AcpChatStatus.closed: frozenset(),
    AcpChatStatus.error: frozenset(),
}


def ensure_chat_transition(current: AcpChatStatus, target: AcpChatStatus) -> None:
    allowed = ACP_CHAT_TRANSITIONS[current]
    if target not in allowed:
        raise ValueError(f"ACP 对话状态不可从 {current.value} 迁移到 {target.value}")


class AgentConnectionStatus(str, enum.Enum):
    """连接测试结论;不含登录秘密。"""

    ready = "ready"
    needs_auth = "needs_auth"
    unsupported = "unsupported"
    failed = "failed"


class CheckpointVerdict(str, enum.Enum):
    """教师检查点答复。"""

    consistent = "consistent"
    mismatch = "mismatch"
