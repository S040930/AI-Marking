"""Excel result-export statistics and workbook structure tests."""

from datetime import datetime
from types import SimpleNamespace
from zipfile import ZipFile

from app.services.result_export import (
    _normalize_criterion,
    _score_band_index,
    build_results_workbook,
    safe_export_filename,
)


def _row(
    row_id: int,
    filename: str,
    score: float,
    max_score: float,
    details: list[dict],
):
    return SimpleNamespace(
        id=row_id,
        original_filename=filename,
        score=score,
        max_score=max_score,
        feedback=f"Feedback {row_id}",
        details=details,
        assessment_suggestion={"mcp_metadata": {"rubric_snapshot_id": "rubric-v1"}},
        reviewed_by="Teacher",
        reviewed_at=datetime(2026, 8, 31, 8, row_id),
        uploaded_at=datetime(2026, 8, 30, 8, row_id),
    )


def test_score_bands_and_normalization_cover_boundaries():
    assert [_score_band_index(value) for value in (0.9, 0.8, 0.7, 0.6, 0.599)] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert _normalize_criterion("  分析\u3000能力  ") == "分析 能力"


def test_workbook_contains_formulas_charts_and_separate_criterion_groups(tmp_path):
    output = tmp_path / "analysis.xlsx"
    rows = [
        _row(
            1,
            "student-a.pdf",
            80,
            100,
            [
                {
                    "rubric_item_id": "content",
                    "criterion": "内容",
                    "score": 40,
                    "max_score": 50,
                    "comment": "Good",
                    "evidence": ["Evidence A"],
                },
                {
                    "criterion": " 分析\u3000能力 ",
                    "score": 40,
                    "max_score": 50,
                    "comment": "Clear",
                    "evidence": [],
                },
            ],
        ),
        _row(
            2,
            "student-b.pdf",
            12.5,
            25,
            [
                {
                    "rubric_item_id": "content",
                    "criterion": "内容",
                    "score": 10,
                    "max_score": 20,
                    "comment": "Partial",
                    "evidence": ["Evidence B"],
                }
            ],
        ),
    ]

    result = build_results_workbook(
        output,
        question_name="测试题目",
        rows=rows,
        pass_threshold=65,
        locale="zh-CN",
        generated_at=datetime(2026, 8, 31, 10, 0),
    )

    assert result.reviewed_count == 2
    assert result.valid_score_count == 2
    assert result.criterion_count == 3
    with ZipFile(output) as workbook:
        names = set(workbook.namelist())
        assert {"xl/charts/chart1.xml", "xl/charts/chart2.xml"} <= names
        workbook_xml = workbook.read("xl/workbook.xml").decode()
        assert "结果分析" in workbook_xml
        assert "成绩总表" in workbook_xml
        assert "评分项明细" in workbook_xml
        analysis_xml = workbook.read("xl/worksheets/sheet1.xml").decode()
        summary_xml = workbook.read("xl/worksheets/sheet2.xml").decode()
        detail_xml = workbook.read("xl/worksheets/sheet3.xml").decode()
        assert "MEDIAN" in analysis_xml
        assert "COUNTIFS" in analysis_xml
        assert "IFERROR(C2/D2,0)" in summary_xml
        assert "conditionalFormatting" in summary_xml
        assert "IFERROR(E2/F2,0)" in detail_xml
        assert "Evidence A" in detail_xml


def test_safe_export_filename_is_ascii_and_stable():
    assert safe_export_filename("中文 / Question-1", datetime(2026, 8, 31, 9, 5)) == (
        "grade_analysis_Question-1_20260831_0905.xlsx"
    )
