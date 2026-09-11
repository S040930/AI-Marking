"""The compact local STDIO MCP workflow for AI-Marking."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from app.mcp.client import ApiClient, review_url
from app.mcp.errors import McpApiError
from app.mcp.inspection import file_sha256, inspect_code_files, inspect_report
from app.mcp.plans import PLAN_TTL_SECONDS, discard_plan, make_plan, read_plan
from app.schemas.mcp import McpAssessmentRequest, McpVisualConfirmationRequest
from app.services.code_manifest import (
    parse_explicit_code_mappings,
)
from app.services.document_storage import (
    ENTRYPOINT_EXTENSIONS,
)

CODE_ENTRY_EXTENSIONS = ENTRYPOINT_EXTENSIONS

logger = logging.getLogger("ai_marking.mcp")
_OPEN_TIMEOUT_SECONDS = 5 * 60
# 单次 wait-ready 长轮询时长:服务端在该窗口内由 NOTIFY 即时唤醒,
# 超时返回当前状态,客户端循环重试直至总预算耗尽。
_WAIT_CHUNK_SECONDS = 60
# wait-ready 不可用(旧后端)时的退化轮询间隔。
_POLL_SECONDS = 10
_active_client: ApiClient | None = None


async def _call(method: str, path: str, **kwargs: object) -> dict | list:
    if _active_client is not None:
        return await _active_client.request(method, path, **kwargs)
    async with ApiClient() as client:
        return await client.request(method, path, **kwargs)


def _client() -> str | None:
    """当前 MCP 宿主客户端标识,由启动脚本以 argv 注入环境变量。"""
    value = os.environ.get("AI_MARKING_MCP_CLIENT", "").strip()
    return value[:64] if value else None


@asynccontextmanager
async def mcp_lifespan(_server: MCPServer) -> AsyncIterator[dict[str, ApiClient]]:
    global _active_client
    async with ApiClient() as client:
        _active_client = client
        try:
            yield {"api_client": client}
        finally:
            _active_client = None


mcp = MCPServer(
    "ai-marking",
    instructions=(
        "AI-Marking 只用于教师明确要求的编程助手批改或修订。"
        "新作业依次调用 prepare_ai_marking_submission、"
        "submit_prepared_ai_marking_submission、open_ai_marking_assignment。"
        "也可用 list_pending_ai_marking_assignments 发现等待评分的作业。"
        "打开作业返回的 grading_policy 是唯一评分规则；读取至 context_complete=true 后再评分。"
        "若打开返回 needs_rubric=true,先调用 save_ai_marking_question_rubric 提取保存题目 rubric,再重新打开作业。"
        "代码可使用 Python、R、Java、C、C++；每题必须标记一个入口，辅助源码随题提交。"
        "编程助手必须在提交前于当前任务中尝试运行每个代码入口；运行结果只留在当前对话，不上传后端。"
        "若作业含代码，评分前必须取得使用者对运行表现与报告一致性的人工确认；未确认时暂停。"
        "编程助手不生成运行日志、视觉比较或复核证据，只保存建议，最终成绩由教师在网页确认。"
        "教师要求复核已有建议时，打开作业读取 current_assessment，"
        "独立复核后调用 save_ai_marking_assessment_review。"
    ),
    lifespan=mcp_lifespan,
)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
async def health_ai_marking() -> dict:
    """只读验证当前 Agent 能访问正确版本的 AI-Marking MCP。"""
    result = await _call("GET", "/api/mcp/health")
    if not isinstance(result, dict):
        raise McpApiError("AI-Marking 健康检查返回无效响应")
    return result


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
async def prepare_ai_marking_submission(
    question_name: str,
    report_path: str,
    code_file_paths: list[str] | None = None,
    code_mappings: list[dict] | None = None,
) -> dict:
    """只读预检题目、报告和多语言代码入口。"""
    await _call("GET", "/api/mcp/health")
    report = inspect_report(report_path)
    code_files = inspect_code_files(code_file_paths or [])
    provided = code_mappings or []
    try:
        mapping_by_name = parse_explicit_code_mappings(
            provided, [item["filename"] for item in code_files]
        )
    except Exception as exc:  # FastAPI validation is converted to MCP text.
        raise McpApiError(str(getattr(exc, "detail", exc))) from exc
    preflight = await _call(
        "POST",
        "/api/mcp/submission-preflight",
        json={
            "question_name": question_name,
            "code_files": [
                {
                    "filename": item["filename"],
                    **(
                        mapping_by_name.get(item["filename"])
                        or {
                            "question_number": None,
                            "entrypoint": Path(item["filename"]).suffix.lower()
                            in CODE_ENTRY_EXTENSIONS,
                        }
                    ),
                }
                for item in code_files
            ],
        },
    )
    if not isinstance(preflight, dict):
        raise McpApiError("AI-Marking 预检接口返回了无效响应")
    if preflight["status"] != "ready_to_submit":
        return preflight
    # source 仅用于预检时的跨文件 import 校验；提交阶段会重新读取并校验 SHA-256，
    # 不将可能很大的源码正文复制进进程内临时 plan。
    plan_code_files = [
        {key: value for key, value in item.items() if key != "source"}
        for item in code_files
    ]
    plan = make_plan(
        {
            "question_id": preflight["question_id"],
            "question_name": preflight["question_name"],
            "report": report,
            "code_files": plan_code_files,
            "code_manifest": preflight["code_manifest"],
        }
    )
    return {
        "status": "ready_to_submit",
        "question_id": preflight["question_id"],
        "question_name": preflight["question_name"],
        "code_manifest": preflight["code_manifest"],
        "submission_plan": plan,
        "expires_in_seconds": PLAN_TTL_SECONDS,
    }


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
async def submit_prepared_ai_marking_submission(submission_plan: str) -> dict:
    """以预检计划上传作业。这是正常流程唯一的新建写操作。"""
    plan = read_plan(submission_plan)
    files_to_check = [plan["report"], *plan["code_files"]]
    for item in files_to_check:
        path = Path(item["path"])
        if not path.is_file() or file_sha256(path) != item["sha256"]:
            raise McpApiError("预检后的文件已变化或不存在，请重新预检")
    multipart: list[tuple[str, tuple[str, object, str]]] = []
    handles: list[object] = []
    try:
        report_path = Path(plan["report"]["path"])
        report_handle = report_path.open("rb")
        handles.append(report_handle)
        multipart.append(("file", (report_path.name, report_handle, "application/pdf")))
        for item in plan["code_files"]:
            path = Path(item["path"])
            handle = path.open("rb")
            handles.append(handle)
            mime = (
                "application/x-ipynb+json"
                if path.suffix.lower() == ".ipynb"
                else "text/plain"
            )
            multipart.append(("code_files", (path.name, handle, mime)))
        async with ApiClient() as client:
            result = await client.request(
                "POST",
                "/api/submissions",
                files=multipart,
                data={
                    "question_id": str(plan["question_id"]),
                    "code_manifest": json.dumps(
                        plan["code_manifest"], ensure_ascii=False
                    ),
                },
            )
    finally:
        for handle in handles:
            handle.close()  # type: ignore[union-attr]
    if not isinstance(result, dict):
        raise McpApiError("AI-Marking 上传接口返回了无效响应")
    submission_id = int(result["id"])
    discard_plan(submission_plan)
    return {
        **result,
        "submission_id": submission_id,
        "review_url": review_url(submission_id),
    }


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
async def list_pending_ai_marking_assignments(
    cursor: str | None = None, limit: int = 20
) -> dict:
    """列出等待 MCP 评分的作业(按上传时间与 ID 升序,最多 100 条)。

    返回 ``items``(submission_id、题目名、文件名、上传时间)与可选的
    ``next_cursor``。用 ``limit`` 控制每页数量(1–100),用 ``next_cursor``
    读取下一页;配合 ``open_ai_marking_assignment`` 逐个打开作业评分。
    """
    if limit < 1 or limit > 100:
        raise McpApiError("limit 必须在 1–100 之间")
    result = await _call(
        "GET",
        "/api/mcp/pending-assignments",
        params={"cursor": cursor or "", "limit": limit},
    )
    if not isinstance(result, dict):
        raise McpApiError("AI-Marking 待办列表接口返回了无效响应")
    return result


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
async def open_ai_marking_assignment(
    submission_id: int, continuation_token: str | None = None
) -> dict:
    """等待最多五分钟并分页打开可评分作业；用 continuation_token 读取下一页。

    若返回 ``needs_rubric=true``,表示该题目还没有可信 rubric,评分包携带
    题目 OCR 与 ``rubric_handle``;此时先调用
    ``save_ai_marking_question_rubric`` 提取并保存 rubric,再重新打开作业。
    """
    if continuation_token:
        result = await _call(
            "GET",
            f"/api/mcp/submissions/{submission_id}/package",
            params={"continuation_token": continuation_token},
        )
        if not isinstance(result, dict):
            raise McpApiError("AI-Marking 评分包接口返回了无效响应")
        return {**result, "review_url": review_url(submission_id)}

    deadline = time.monotonic() + _OPEN_TIMEOUT_SECONDS
    while True:
        result = await _call("GET", f"/api/mcp/submissions/{submission_id}/package")
        if not isinstance(result, dict):
            raise McpApiError("AI-Marking 评分包接口返回了无效响应")
        if result.get("needs_rubric") or result.get("status") in {
            "awaiting_mcp",
            "ready_for_review",
            "reviewed",
            "failed",
        }:
            return {**result, "review_url": review_url(submission_id)}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                **result,
                "review_url": review_url(submission_id),
                "still_processing": True,
                "message": "OCR 仍在处理中；请再次调用同一工具。",
            }
        await _wait_until_ready(submission_id, min(_WAIT_CHUNK_SECONDS, remaining))


async def _wait_until_ready(submission_id: int, timeout: float) -> None:
    """阻塞等待作业状态变更;wait-ready 不可用时退化为固定间隔轮询。

    服务端长轮询由 NOTIFY 即时唤醒,避免了每 10s 一次的全量评分包查询。
    """
    try:
        await _call(
            "GET",
            f"/api/mcp/submissions/{submission_id}/wait-ready",
            params={"timeout": timeout},
        )
    except McpApiError:
        logger.info(
            "wait-ready 端点不可用,退化为 %ss 轮询 [submission=%s]",
            _POLL_SECONDS,
            submission_id,
        )
        await asyncio.sleep(min(_POLL_SECONDS, timeout))


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True
    )
)
async def save_ai_marking_question_rubric(
    question_id: str,
    handle: str,
    status: Literal["complete", "absent_or_ambiguous"],
    items: list[dict] | None = None,
    total_max_score: float | None = None,
) -> dict:
    """保存从题目 OCR 提取的 rubric(open 返回 needs_rubric 时调用)。

    ``question_id`` 与 ``handle`` 来自 ``open_ai_marking_assignment`` 的
    needs_rubric 响应。``status=complete`` 时逐项提交 ``criterion`` /
    ``max_score`` / ``details`` 与必须包含该项满分的 OCR 原文
    ``source_quote``;服务端确定性校验通过后写入题目权威快照。
    ``status=absent_or_ambiguous`` 表示题目没有明确 rubric,服务端会改走
    配置 rubric 或内置默认。保存后须重新调用 ``open_ai_marking_assignment``
    以新 rubric 快照评分。
    """
    result = await _call(
        "PUT",
        f"/api/mcp/questions/{question_id}/rubric",
        json={
            "handle": handle,
            "status": status,
            "items": items or [],
            "total_max_score": total_max_score,
        },
    )
    if not isinstance(result, dict):
        raise McpApiError("AI-Marking 保存 rubric 接口返回了无效响应")
    return result


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True
    )
)
async def save_ai_marking_assessment(
    submission_id: int,
    grading_handle: str,
    assessment: McpAssessmentRequest,
) -> dict:
    """保存外部编程助手建议；request_id 与 rubric snapshot 必须来自当前评分包。

    含代码作业的每个打分明细 ``evidence_refs`` 必须带至少一条服务端可逐字定位
    的证据:代码用 ``{"type": "source_line", "filename", "line", "end_line?", "quote"}``
    (行号按单个代码文件计,见评分包 ``grading_policy.evidence_format``),报告用
    ``{"type": "report_quote", "quote"}``(逐字取自评分包 submission OCR 段)。
    引文不可定位时服务端返回 422 并逐条列出原因。
    """
    result = await _call(
        "PUT",
        f"/api/mcp/submissions/{submission_id}/assessment-v2",
        json={
            "grading_handle": grading_handle,
            "assessment": assessment.model_dump(mode="json"),
            "client": _client(),
        },
    )
    if not isinstance(result, dict):
        raise McpApiError("AI-Marking 保存接口返回了无效响应")
    return {**result, "review_url": review_url(submission_id)}


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True
    )
)
async def save_ai_marking_assessment_review(
    submission_id: int,
    grading_handle: str,
    verdict: Literal["agree", "partial", "disagree"],
    summary: str,
    items: list[dict],
    confidence: float = 0,
) -> dict:
    """保存对当前评分建议的独立复核结论(教师要求复核时调用)。

    先用 ``open_ai_marking_assignment`` 完整读取评分包(含
    ``current_assessment`` 建议原文),独立复核每个评分项后调用本工具。
    ``items`` 逐项引用当前 rubric 的 ``rubric_item_id``,每项携带
    ``verdict``(agree/disagree)与 ``comment``;有分歧的项可附
    ``suggested_score``(不超过该项满分)。``verdict`` 为总体结论:
    全部同意用 agree,部分分歧用 partial,整体不可靠用 disagree。
    复核只供教师参考,不修改建议本身。
    """
    result = await _call(
        "PUT",
        f"/api/mcp/submissions/{submission_id}/assessment-review",
        json={
            "grading_handle": grading_handle,
            "verdict": verdict,
            "summary": summary,
            "confidence": confidence,
            "items": items,
            "client": _client(),
        },
    )
    if not isinstance(result, dict):
        raise McpApiError("AI-Marking 保存复核接口返回了无效响应")
    return {**result, "review_url": review_url(submission_id)}


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True
    )
)
async def confirm_ai_marking_visual_review(
    submission_id: int,
    grading_handle: str,
    verdict: Literal["consistent", "mismatch"],
    note: str | None = None,
) -> dict:
    """记录本地运行表现与报告描述的一致性人工核验，含代码作业必须在保存前调用。"""
    confirmation = McpVisualConfirmationRequest(
        grading_handle=grading_handle, verdict=verdict, note=note
    )
    result = await _call(
        "POST",
        f"/api/mcp/submissions/{submission_id}/visual-confirmation",
        json=confirmation.model_dump(mode="json"),
    )
    if not isinstance(result, dict):
        raise McpApiError("AI-Marking 视觉核验接口返回了无效响应")
    return result


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    mcp.run()


if __name__ == "__main__":
    main()
