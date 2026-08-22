"""题目标识符（question_id）生成与校验。

question_id 是题目主键,基于创建时 ``original_filename`` 的去扩展名 stem
生成稳定 slug(见 Question 模型)。替换文件 / 重试 OCR 只更新文件字段,
不改动 id,因此历史外键引用始终有效。重名题目通过唯一性校验在 API 层
拒绝,slug 自身不做自动加后缀。

slug 只保留中文 / 字母 / 数字 / ``-`` / ``_``,空格与非法字符归一为 ``-``,
以保证在 URL 路径、multipart Form 字段与 SSE 事件负载中安全传输。
"""

from __future__ import annotations

import re
import unicodedata

# 与 questions.id 列宽保持一致(见 app/models/question.py)。
MAX_QUESTION_ID_LEN = 100
_SEPARATOR = "-"

# 只保留中文字符、字母、数字、连字符与下划线;其余全部视为非法字符。
_KEEP_PATTERN = re.compile(r"[^\w\u4e00-\u9fff-]", re.UNICODE)
_MULTI_DASH = re.compile(r"-{2,}")


def _normalize_stem(value: str) -> str:
    """NFKC 归一化并去除首尾空白,作为 slug 输入。"""
    return unicodedata.normalize("NFKC", value).strip()


def slugify_stem(stem: str) -> str:
    """把文件 stem 转成 URL 安全的题目 id slug。

    - 非法字符(空格、符号等)替换为 ``-``
    - 连续多个 ``-`` 合并为一个
    - 首尾 ``-`` 去除
    - 超长截断到 ``MAX_QUESTION_ID_LEN``(截断在 UTF-8 安全边界)
    """
    raw = _normalize_stem(stem)
    # 先把非法字符替换为分隔符,再归一化重复分隔符。
    slugged = _KEEP_PATTERN.sub(_SEPARATOR, raw)
    slugged = _MULTI_DASH.sub(_SEPARATOR, slugged).strip(_SEPARATOR)
    if len(slugged) > MAX_QUESTION_ID_LEN:
        slugged = slugged[:MAX_QUESTION_ID_LEN].rstrip(_SEPARATOR)
    return slugged


def build_question_id(original_filename: str) -> str:
    """从原始文件名生成题目 id(去扩展名的 slug)。

    ``Question.original_filename`` 允许不含扩展名,此时直接使用整个文件名。
    文件名形如 ``DTS208TC_CW1_Paper.pdf`` → ``DTS208TC_CW1_Paper``。
    """
    stem = _normalize_stem(original_filename)
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return slugify_stem(stem)
