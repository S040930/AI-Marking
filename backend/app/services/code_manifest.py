"""代码文件 → 小题编号映射的共享校验。

网页端上传（``submissions.py``）、MCP 预检（``api/mcp.py``）与本地 MCP
客户端（``app/mcp/server.py``）共用同一套规则：Q<n> 文件名自动推导题号、
每道题必须且只能有一个入口、非 Q<n> 文件名必须出现在题目 OCR 中。

所有校验抛 ``ValueError``（携带面向用户的中文消息），由调用方包装成
``HTTPException`` 或 ``McpApiError``。
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Mapping

from app.services.document_storage import (
    validate_code_filenames,
)

# 与题目 OCR 中提取的代码文件名匹配:覆盖全部支持扩展名。
_CODE_NAME_PATTERN = re.compile(
    r"[A-Za-z0-9_.-]+\.(?:py|ipynb|r|java|c|cc|cpp|cxx|h|hh|hpp|hxx)\b", re.I
)
_AUTO_Q_PATTERN = re.compile(r"q(\d+)\.[A-Za-z0-9_.-]+$", re.I)


def _normalized_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def auto_question_number(filename: str) -> int | None:
    """从 ``q<n>.<ext>`` 文件名推导题号,否则返回 None。"""
    match = _AUTO_Q_PATTERN.match(filename)
    return int(match.group(1)) if match else None


def parse_explicit_code_mappings(
    entries: list, filenames: list[str]
) -> dict[str, dict[str, object]]:
    """类型校验 + 覆盖校验(映射不得引用未提交文件)。

    返回 filename → {question_number, entrypoint};``resolve_code_manifest``
    负责自动推导与 OCR 校验。供网页端 JSON 解析与本地 MCP 客户端共用。
    """
    normalized = validate_code_filenames(filenames)
    mapping: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            raise ValueError("code_manifest 项格式错误")
        name = unicodedata.normalize("NFKC", entry["filename"])
        number = entry.get("question_number")
        entrypoint = entry.get("entrypoint", True)
        if not isinstance(number, int) or number < 1:
            raise ValueError("question_number 必须为正整数")
        if not isinstance(entrypoint, bool):
            raise ValueError("entrypoint 必须为布尔值")
        mapping[name] = {"question_number": number, "entrypoint": entrypoint}
    if set(mapping) - set(normalized):
        raise ValueError("code_manifest 文件名不匹配")
    return mapping


def parse_code_manifest_json(
    raw_manifest: str | None,
    filenames: list[str],
    question_text: str,
) -> dict[str, dict[str, object]]:
    """解析网页端 ``code_manifest`` JSON 字符串并校验映射。"""
    if raw_manifest is None:
        return resolve_code_manifest(filenames=filenames, question_text=question_text)
    try:
        entries = json.loads(raw_manifest)
    except json.JSONDecodeError as exc:
        raise ValueError("code_manifest 不是合法 JSON") from exc
    if not isinstance(entries, list):
        raise ValueError("code_manifest 必须是数组")
    explicit = parse_explicit_code_mappings(entries, filenames)
    return resolve_code_manifest(
        filenames=filenames, question_text=question_text, explicit=explicit
    )


def resolve_code_manifest(
    *,
    filenames: list[str],
    question_text: str,
    explicit: dict[str, Mapping[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    """校验并推导代码文件 → 小题映射。

    返回以 NFKC 归一化文件名为键、值为 ``{"question_number", "entrypoint"}`` 的
    字典。

    - ``explicit=None``(网页端无 manifest):全部文件必须按 Q<n> 自动推导。
    - ``explicit`` 提供(网页端有 manifest / MCP 预检):必须覆盖全部文件,
      qN 文件仍可经调用方补全进 explicit。
    """
    normalized = validate_code_filenames(filenames)
    if not normalized:
        return {}
    explicit = explicit or {}

    mapping: dict[str, dict[str, object]] = {}
    for name in normalized:
        if name in explicit:
            mapping[name] = {
                "question_number": int(explicit[name]["question_number"]),
                "entrypoint": bool(explicit[name].get("entrypoint", True)),
            }
        elif explicit and auto_question_number(name) is not None:
            # 显式映射已提供但未包含该 qN 文件:调用方负责补全;若遗漏则拒绝。
            raise ValueError("code_manifest 未覆盖全部代码文件")
        elif not explicit and auto_question_number(name) is not None:
            mapping[name] = {"question_number": auto_question_number(name), "entrypoint": True}
        else:
            raise ValueError(
                "未提供 code_manifest 时，代码入口必须使用 Q<n>.<语言扩展名> 命名"
            )

    by_question: dict[int, list[dict[str, object]]] = {}
    for item in mapping.values():
        by_question.setdefault(int(item["question_number"]), []).append(item)
    for number, group in by_question.items():
        if sum(bool(item["entrypoint"]) for item in group) != 1:
            raise ValueError(f"第 {number} 题必须且只能有一个代码入口文件")

    # 非 qN 文件名必须出现在题目 OCR 中(显式代码名集合优先,其次整段文本子串)。
    # qN 文件名通过文件名自标识题号,无需 OCR 佐证。
    explicit_names = {
        _normalized_name(match) for match in _CODE_NAME_PATTERN.findall(question_text)
    }
    normalized_question = _normalized_name(question_text)
    for name in normalized:
        if auto_question_number(name) is not None:
            continue
        if explicit_names and name.casefold() not in explicit_names:
            raise ValueError(
                f"代码文件 {name} 未在题目中出现，请按题目指定名称上传"
            )
        if not explicit_names and name.casefold() not in normalized_question:
            raise ValueError(f"代码文件 {name} 无法映射到题目")
    return mapping
