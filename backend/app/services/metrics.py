"""Prometheus 业务指标定义与埋点工具。

HTTP 层指标(request 数、延迟、状态码分布)由 ``prometheus-fastapi-instrumentator``
在 ``main.py`` 自动埋点,本模块仅定义业务关键节点指标:

- 队列深度 (按 status 分桶):queued / running / dead
- 任务执行时长与重试/死信计数
- OCR 调用结果与熔断信号
- LLM 各节点调用结果与时长
- 任务租约丢失计数

命名约定:``marking_*`` 表示 worker/队列相关;``ocr_*`` / ``llm_*`` 表示外部
服务调用。所有指标均为模块级单例,导入即注册到默认 registry。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# 队列指标
# ---------------------------------------------------------------------------

queue_depth = Gauge(
    "marking_queue_depth",
    "后台任务队列深度",
    labelnames=("status",),
)

# ---------------------------------------------------------------------------
# 任务执行指标
# ---------------------------------------------------------------------------

task_duration = Histogram(
    "marking_task_duration_seconds",
    "后台任务执行时长(从 claim 到 complete/dead)",
    labelnames=("job_type",),
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
)

task_retries = Counter(
    "marking_task_retries_total",
    "任务退避重试次数(未进入死信前的重试)",
    labelnames=("job_type",),
)

dead_jobs = Counter(
    "marking_dead_jobs_total",
    "进入死信的任务总数",
    labelnames=("job_type",),
)

lease_lost = Counter(
    "marking_lease_lost_total",
    "任务租约丢失次数(心跳续租失败或被其他 worker 抢占)",
)

# ---------------------------------------------------------------------------
# OCR 指标
# ---------------------------------------------------------------------------

ocr_calls = Counter(
    "ocr_calls_total",
    "PaddleOCR-VL 调用次数",
    labelnames=("result",),  # success / failure / circuit_open
)

# ---------------------------------------------------------------------------
# LLM 指标
# ---------------------------------------------------------------------------

llm_calls = Counter(
    "llm_calls_total",
    "LLM 调用次数",
    labelnames=("node", "result"),  # node: grade/critic/chat, result: success/failure
)

llm_duration = Histogram(
    "llm_duration_seconds",
    "LLM 调用时长",
    labelnames=("node",),
    buckets=(1, 5, 10, 30, 60, 120),
)

# Codex MCP 评分指标。标签保持低基数，不包含 submission、文件名或模型名。
mcp_tool_calls = Counter(
    "mcp_tool_calls_total",
    "MCP 适配器业务调用次数",
    labelnames=("tool", "result"),
)
mcp_tool_duration = Histogram(
    "mcp_tool_duration_seconds",
    "MCP 适配器业务调用耗时",
    labelnames=("tool",),
)
codex_assessment_saves = Counter(
    "codex_assessment_saves_total",
    "Codex 评分建议保存次数",
    labelnames=("kind",),
)
codex_assessment_conflicts = Counter(
    "codex_assessment_conflicts_total",
    "Codex 评分建议冲突次数",
    labelnames=("reason",),
)
codex_waiting_submissions = Gauge(
    "codex_waiting_submissions",
    "等待 Codex 评分的提交数量",
)
