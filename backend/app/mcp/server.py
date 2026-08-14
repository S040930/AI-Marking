"""The compact local STDIO MCP workflow for AI-Marking."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from app.mcp.client import ApiClient, review_url
from app.mcp.errors import McpApiError
from app.schemas.mcp import McpSimpleAssessmentRequest, McpVisualConfirmationRequest
from app.services.code_manifest import (
    auto_question_number,
    parse_explicit_code_mappings,
)
from app.services.document_storage import (
    CODE_EXTENSIONS,
    ENTRYPOINT_EXTENSIONS,
    MAX_CODE_FILE_BYTES,
    MAX_CODE_FILES,
    MAX_CODE_TOTAL_BYTES,
    validate_code_filenames,
)

CODE_ENTRY_EXTENSIONS = ENTRYPOINT_EXTENSIONS
CODE_SUPPORT_EXTENSIONS = CODE_EXTENSIONS

logger = logging.getLogger("ai_marking.mcp")
_PLAN_TTL_SECONDS = 30 * 60
_MAX_PREPARED_PLANS = 256
_POLL_SECONDS = 10
_OPEN_TIMEOUT_SECONDS = 5 * 60
_prepared_plans: dict[str, dict] = {}
_active_client: ApiClient | None = None


async def _call(method: str, path: str, **kwargs: object) -> dict | list:
    if _active_client is not None:
        return await _active_client.request(method, path, **kwargs)
    async with ApiClient() as client:
        return await client.request(method, path, **kwargs)


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
        "AI-Marking 只用于教师明确要求的 Codex 批改或修订。"
        "新作业依次调用 prepare_ai_marking_submission、"
        "submit_prepared_ai_marking_submission、open_ai_marking_assignment。"
        "打开作业返回的 grading_policy 是唯一评分规则；读取至 context_complete=true 后再评分。"
        "代码可使用 Python、R、Java、C、C++；每题必须标记一个入口，辅助源码随题提交。"
        "Codex 必须在提交前于当前任务中尝试运行每个代码入口；运行结果只留在当前对话，不上传后端。"
        "若作业含代码，评分前必须取得使用者对运行表现与报告一致性的人工确认；未确认时暂停。"
        "Codex 不生成运行日志、视觉比较或复核证据，只保存建议，最终成绩由教师在网页确认。"
    ),
    lifespan=mcp_lifespan,
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_absolute_file(raw: str, *, label: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise McpApiError(f"{label} 必须是本机绝对路径")
    path = path.resolve()
    if not path.is_file():
        raise McpApiError(f"{label} 必须指向存在的普通文件")
    return path


def _inspect_report(raw: str) -> dict:
    path = _require_absolute_file(raw, label="report_path")
    if path.suffix.lower() != ".pdf":
        raise McpApiError("report_path 只接受 PDF 文件")
    size = path.stat().st_size
    if size <= 0 or size > 50 * 1024 * 1024:
        raise McpApiError("PDF 大小必须在 1 到 50 MB 之间")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise McpApiError("文件内容不是有效 PDF")
    return {"path": str(path), "filename": path.name, "sha256": _file_sha256(path)}


def _notebook_source(value: str) -> str:
    try:
        notebook = json.loads(value)
    except json.JSONDecodeError as exc:
        raise McpApiError("Notebook 不是合法 JSON") from exc
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        raise McpApiError("Notebook 不是合法 Notebook")
    pieces: list[str] = []
    for cell in notebook["cells"]:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        pieces.append("".join(source) if isinstance(source, list) else str(source))
    return "\n".join(pieces)


def _reject_cross_file_imports(files: list[dict]) -> None:
    modules = {
        Path(item["filename"]).stem: number
        for item in files
        if (number := auto_question_number(item["filename"])) is not None
    }
    for item in files:
        source = item["source"]
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # Syntax errors remain student execution evidence.
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".", 1)[0]]
            current_match = auto_question_number(item["filename"])
            if current_match is not None and any(
                name in modules and modules[name] != current_match for name in names
            ):
                raise McpApiError("每题代码必须独立，不同小题不能互相 import")


def _inspect_code_files(paths: list[str]) -> list[dict]:
    if len(paths) > MAX_CODE_FILES:
        raise McpApiError(f"代码文件最多 {MAX_CODE_FILES} 个")
    inspected: list[dict] = []
    total_size = 0
    names: list[str] = []
    for raw in paths:
        path = _require_absolute_file(raw, label="code_file_paths")
        if path.suffix.lower() not in CODE_SUPPORT_EXTENSIONS:
            raise McpApiError("代码文件类型不受支持")
        size = path.stat().st_size
        total_size += size
        if size <= 0 or size > MAX_CODE_FILE_BYTES or total_size > MAX_CODE_TOTAL_BYTES:
            raise McpApiError("代码文件超过大小限制")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise McpApiError(f"代码文件 {path.name} 必须是 UTF-8 文本") from exc
        if "\x00" in text:
            raise McpApiError(f"代码文件 {path.name} 含有非法 NUL 字节")
        source = _notebook_source(text) if path.suffix.lower() == ".ipynb" else text
        names.append(path.name)
        inspected.append(
            {"path": str(path), "filename": path.name, "sha256": _file_sha256(path), "source": source}
        )
    try:
        validate_code_filenames(names)
    except Exception as exc:  # FastAPI validation is converted to MCP text.
        raise McpApiError(str(getattr(exc, "detail", exc))) from exc
    _reject_cross_file_imports(inspected)
    return inspected


def _make_plan(plan: dict) -> str:
    _purge_expired_plans()
    if len(_prepared_plans) >= _MAX_PREPARED_PLANS:
        raise McpApiError("待提交预检计划已达到上限，请先提交现有计划或稍后重试")
    plan_id = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _PLAN_TTL_SECONDS
    _prepared_plans[plan_id] = {**plan, "expires_at": expires_at}
    return plan_id


def _purge_expired_plans() -> None:
    now = int(time.time())
    for key, plan in list(_prepared_plans.items()):
        if plan.get("expires_at", 0) < now:
            _prepared_plans.pop(key, None)


def _read_plan(token: str) -> dict:
    _purge_expired_plans()
    plan_id = token
    plan = _prepared_plans.get(plan_id)
    if not plan:
        raise McpApiError("submission_plan 无效或 MCP 已重启，请重新预检")
    return plan


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
async def prepare_ai_marking_submission(
    question_name: str,
    report_path: str,
    code_file_paths: list[str] | None = None,
    code_mappings: list[dict] | None = None,
) -> dict:
    """只读预检题目、报告和多语言代码入口。"""
    await _call("GET", "/api/mcp/health")
    report = _inspect_report(report_path)
    code_files = _inspect_code_files(code_file_paths or [])
    provided = code_mappings or []
    try:
        mapping_by_name = parse_explicit_code_mappings(provided, [item["filename"] for item in code_files])
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
                    **(mapping_by_name.get(item["filename"]) or {
                        "question_number": None,
                        "entrypoint": Path(item["filename"]).suffix.lower() in CODE_ENTRY_EXTENSIONS,
                    }),
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
    plan = _make_plan(
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
        "expires_in_seconds": _PLAN_TTL_SECONDS,
    }


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
async def submit_prepared_ai_marking_submission(submission_plan: str) -> dict:
    """以预检计划上传作业。这是正常流程唯一的新建写操作。"""
    plan = _read_plan(submission_plan)
    files_to_check = [plan["report"], *plan["code_files"]]
    for item in files_to_check:
        path = Path(item["path"])
        if not path.is_file() or _file_sha256(path) != item["sha256"]:
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
            mime = "application/x-ipynb+json" if path.suffix.lower() == ".ipynb" else "text/plain"
            multipart.append(("code_files", (path.name, handle, mime)))
        async with ApiClient() as client:
            result = await client.request(
                "POST",
                "/api/submissions",
                files=multipart,
                data={
                    "question_id": str(plan["question_id"]),
                    "grading_mode": "codex",
                    "code_manifest": json.dumps(plan["code_manifest"], ensure_ascii=False),
                },
            )
    finally:
        for handle in handles:
            handle.close()  # type: ignore[union-attr]
    if not isinstance(result, dict):
        raise McpApiError("AI-Marking 上传接口返回了无效响应")
    submission_id = int(result["id"])
    _prepared_plans.pop(submission_plan.split(".", 1)[0], None)
    return {**result, "submission_id": submission_id, "review_url": review_url(submission_id)}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
async def open_ai_marking_assignment(
    submission_id: int, continuation_token: str | None = None
) -> dict:
    """等待最多五分钟并分页打开可评分作业；用 continuation_token 读取下一页。"""
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
        if result.get("status") in {"awaiting_codex", "ready_for_review", "reviewed", "failed"}:
            return {**result, "review_url": review_url(submission_id)}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                **result,
                "review_url": review_url(submission_id),
                "still_processing": True,
                "message": "OCR 仍在处理中；请再次调用同一工具。",
            }
        await asyncio.sleep(min(_POLL_SECONDS, remaining))


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True
    )
)
async def save_ai_marking_assessment(
    submission_id: int,
    grading_handle: str,
    assessment: McpSimpleAssessmentRequest,
) -> dict:
    """保存 Codex 建议；request_id 与 rubric snapshot 必须来自当前评分包。"""
    result = await _call(
        "PUT",
        f"/api/mcp/submissions/{submission_id}/assessment-v2",
        json={
            "grading_handle": grading_handle,
            "assessment": assessment.model_dump(mode="json"),
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
async def confirm_ai_marking_visual_review(
    submission_id: int,
    grading_handle: str,
    verdict: Literal["consistent", "mismatch"],
    note: str | None = None,
) -> dict:
    """记录 Codex 本地运行表现与报告描述的一致性人工核验，含代码作业必须在保存前调用。"""
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


@mcp.prompt()
def grade_assignment(submission_id: int) -> str:
    """Generate the compact workflow for a newly uploaded or existing assignment."""
    return f"""使用 AI-Marking 批改或修订作业 #{submission_id}：
1. 调用 open_ai_marking_assignment({submission_id})；若仍在处理，自动再次调用同一工具。
2. 使用 continuation_token 重复打开，直到 context_complete=true；遵守评分包中的 grading_policy。
3. 若作业含代码，先向使用者提问 Codex 本地运行表现与报告描述是否一致：仅“已检查且一致”，或“已检查且存在不一致”并要求自由文字说明；尚未检查、含糊或未回答时暂停。
4. 收到有效回答后，必须先调用 confirm_ai_marking_visual_review(submission_id, grading_handle, verdict, note)，再评分。
5. 存在不一致时只使用使用者明确说明的差异，不推断其他差异，不生成任何视觉比较或复核证据。
6. 若评分包 grading_policy.review_required 为 true，完成第一遍后进行第二遍独立复核，并在 self_check 中标记 second_pass_completed=true；最后调用 save_ai_marking_assessment，使用最后返回的 grading_handle。
7. 返回 review_url；不要确认最终成绩，教师必须在网页确认。"""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
