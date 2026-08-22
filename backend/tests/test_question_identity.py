"""题目 id(slug)生成规则测试。"""

from app.services.question_identity import (
    MAX_QUESTION_ID_LEN,
    build_question_id,
    slugify_stem,
)


def test_slugify_replaces_spaces_with_dash():
    assert slugify_stem("DTS208TC CW1 Paper") == "DTS208TC-CW1-Paper"


def test_slugify_strips_leading_trailing_separators():
    assert slugify_stem("  foo  ") == "foo"


def test_slugify_merges_repeated_dashes():
    assert slugify_stem("foo---bar") == "foo-bar"


def test_slugify_removes_illegal_symbols():
    assert slugify_stem("foo@#$%bar") == "foo-bar"


def test_slugify_keeps_underscore_and_chinese():
    assert slugify_stem("期中_试卷_2026") == "期中_试卷_2026"


def test_slugify_truncates_long_stem():
    long_stem = "a" * (MAX_QUESTION_ID_LEN + 50)
    result = slugify_stem(long_stem)
    assert len(result) == MAX_QUESTION_ID_LEN


def test_slugify_nfkc_normalizes_fullwidth():
    # 全角空格 NFKC 后归一为半角空格 → 连字符
    assert slugify_stem("算法\u3000作业") == "算法-作业"


def test_build_question_id_uses_stem_without_extension():
    assert build_question_id("DTS208TC_CW1_Paper.pdf") == "DTS208TC_CW1_Paper"


def test_build_question_id_allows_dotless_filename():
    assert build_question_id("Midterm") == "Midterm"


def test_build_question_id_handles_uppercase_extension():
    assert build_question_id("Report.PDF") == "Report"


def test_build_question_id_empty_stem_returns_empty():
    assert build_question_id("") == ""
