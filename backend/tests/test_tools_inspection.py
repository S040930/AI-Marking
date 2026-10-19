"""P1 只读检查工具测试：切片、搜索、代码真实行号、rubric 与教师答复。"""

from __future__ import annotations

from app.agent.tools.inspection import InspectionContext, build_inspection_tools

ARTIFACT = {
    "version": 1,
    "sections": [
        {"name": "question", "text": "请分析数据并写报告", "chars": 9},
        {
            "name": "submission",
            "text": "第一段。\nThe model achieved 92% accuracy.\n结论。",
            "chars": 47,
        },
        {"name": "source", "text": "", "chars": 0},
    ],
    "code_files": [
        {
            "filename": "task1.py",
            "question_number": 1,
            "source_text": "import pandas as pd\n\ndf = pd.read_csv('data.csv')\nprint(df.head())\n",
            "sha256": "abc",
            "line_count": 4,
        }
    ],
    "rubric": {
        "snapshot_id": "snap-1",
        "items": [
            {
                "rubric_item_id": "item-0001",
                "criterion": "结果准确性",
                "max_score": 10,
                "details": "报告结论与数据一致",
            }
        ],
    },
    "has_code": True,
}


def _tools(teacher=None):
    return build_inspection_tools(
        InspectionContext(artifact=ARTIFACT, teacher=teacher)
    )


def test_read_slice_paginates() -> None:
    result = _tools().execute(
        "read_package_slice", {"section": "submission", "offset": 0, "length": 10}
    )
    assert result.ok
    assert result.output["text"]
    assert result.output["complete"] is False
    assert result.output["next_offset"] == 10


def test_search_returns_line_and_excerpt() -> None:
    result = _tools().execute("search_package", {"query": "92%"})
    assert result.ok
    match = result.output["matches"][0]
    assert match["section"] == "submission"
    assert match["line"] == 2
    assert "92%" in match["excerpt"]


def test_read_code_lines_uses_per_file_line_numbers() -> None:
    result = _tools().execute(
        "read_code_lines", {"filename": "TASK1.PY", "start_line": 3, "end_line": 4}
    )
    assert result.ok
    assert result.output["total_lines"] == 4
    assert result.output["lines"] == [
        {"line": 3, "text": "df = pd.read_csv('data.csv')"},
        {"line": 4, "text": "print(df.head())"},
    ]


def test_read_unknown_code_file_fails_with_choices() -> None:
    result = _tools().execute("read_code_lines", {"filename": "missing.py"})
    assert result.ok is False
    assert "task1.py" in result.error


def test_read_rubric_item_and_teacher_answer() -> None:
    tools = _tools(teacher={"verdict": "mismatch", "note": "第 3 行输出不同"})
    item = tools.execute("read_rubric_item", {"rubric_item_id": "item-0001"})
    assert item.ok and item.output["max_score"] == 10

    answer = tools.execute("read_teacher_answer", {})
    assert answer.ok
    assert answer.output == {
        "available": True,
        "verdict": "mismatch",
        "note": "第 3 行输出不同",
    }


def test_missing_teacher_answer_is_explicit() -> None:
    result = _tools().execute("read_teacher_answer", {})
    assert result.ok
    assert result.output == {"available": False}
