"""代码文件 → 小题映射共享校验测试。"""

import pytest

from app.services.code_manifest import (
    auto_question_number,
    parse_code_manifest_json,
    parse_explicit_code_mappings,
    resolve_code_manifest,
)

QUESTION_TEXT = (
    "Task 1 uses task1.py and task2.py; Task 2 uses helper.py and "
    "netflix_teaching_dataset.csv."
)


def test_auto_question_number_derives_from_qn_filename():
    assert auto_question_number("Q1.py") == 1
    assert auto_question_number("q3.java") == 3
    assert auto_question_number("q12.ipynb") == 12
    assert auto_question_number("task1.py") is None
    assert auto_question_number("q1.h") == 1  # 头文件仍可从 stem 推导


def test_qn_filenames_auto_map_without_explicit_manifest():
    result = resolve_code_manifest(
        filenames=["Q1.py", "Q2.java"],
        question_text="",
        explicit=None,
    )
    assert result == {
        "Q1.py": {"question_number": 1, "entrypoint": True},
        "Q2.java": {"question_number": 2, "entrypoint": True},
    }


def test_explicit_mapping_overrides_auto_derivation():
    result = resolve_code_manifest(
        filenames=["Q1.py"],
        question_text=QUESTION_TEXT,
        explicit={"Q1.py": {"question_number": 5, "entrypoint": True}},
    )
    assert result["Q1.py"]["question_number"] == 5


def test_explicit_non_qn_file_must_appear_in_question():
    # 显式映射覆盖全部文件,且文件名出现在题目 OCR 中
    result = resolve_code_manifest(
        filenames=["task1.py", "helper.py"],
        question_text=QUESTION_TEXT,
        explicit={
            "task1.py": {"question_number": 1, "entrypoint": True},
            "helper.py": {"question_number": 2, "entrypoint": True},
        },
    )
    assert result["task1.py"]["entrypoint"] is True
    assert result["helper.py"]["entrypoint"] is True


def test_duplicate_entrypoint_for_same_question_rejected():
    with pytest.raises(ValueError, match="必须且只能有一个代码入口文件"):
        resolve_code_manifest(
            filenames=["Q1.py", "Q2.py", "Q3.py"],
            question_text="",
            explicit={
                "Q1.py": {"question_number": 1, "entrypoint": True},
                "Q2.py": {"question_number": 1, "entrypoint": True},
                "Q3.py": {"question_number": 2, "entrypoint": True},
            },
        )


def test_missing_entrypoint_for_question_rejected():
    with pytest.raises(ValueError, match="必须且只能有一个代码入口文件"):
        resolve_code_manifest(
            filenames=["Q1.py", "Q2.py"],
            question_text="",
            explicit={
                "Q1.py": {"question_number": 1, "entrypoint": False},
                "Q2.py": {"question_number": 2, "entrypoint": True},
            },
        )


def test_non_qn_filename_not_in_question_rejected():
    with pytest.raises(ValueError, match="未在题目中出现"):
        resolve_code_manifest(
            filenames=["Q1.py", "unknown.py"],
            question_text=QUESTION_TEXT,
            explicit={
                "Q1.py": {"question_number": 1, "entrypoint": True},
                "unknown.py": {"question_number": 2, "entrypoint": True},
            },
        )


def test_non_qn_file_requires_explicit_manifest():
    with pytest.raises(ValueError, match="必须使用 Q<n>"):
        resolve_code_manifest(
            filenames=["task1.py"],
            question_text=QUESTION_TEXT,
            explicit=None,
        )


def test_explicit_mapping_not_covering_all_files_rejected():
    # 显式映射已提供但未覆盖 Q2.py(即便 Q2.py 可自动推导),应拒绝
    with pytest.raises(ValueError, match="未覆盖全部代码文件"):
        resolve_code_manifest(
            filenames=["Q1.py", "Q2.py"],
            question_text="",
            explicit={"Q1.py": {"question_number": 1, "entrypoint": True}},
        )


def test_json_manifest_parsing_and_validation():
    json_str = '[{"filename":"task1.py","question_number":1}]'
    result = parse_code_manifest_json(
        json_str,
        filenames=["task1.py"],
        question_text=QUESTION_TEXT,
    )
    assert result["task1.py"] == {"question_number": 1, "entrypoint": True}


def test_json_manifest_invalid_json_rejected():
    with pytest.raises(ValueError, match="不是合法 JSON"):
        parse_code_manifest_json(
            "{not-json",
            filenames=["task1.py"],
            question_text=QUESTION_TEXT,
        )


def test_json_manifest_not_list_rejected():
    with pytest.raises(ValueError, match="必须是数组"):
        parse_code_manifest_json(
            '{"filename":"task1.py"}',
            filenames=["task1.py"],
            question_text=QUESTION_TEXT,
        )


def test_parse_explicit_code_mappings_validates_types():
    with pytest.raises(ValueError, match="question_number 必须为正整数"):
        parse_explicit_code_mappings(
            [{"filename": "task1.py", "question_number": 0}],
            filenames=["task1.py"],
        )
    with pytest.raises(ValueError, match="entrypoint 必须为布尔值"):
        parse_explicit_code_mappings(
            [{"filename": "task1.py", "question_number": 1, "entrypoint": "yes"}],
            filenames=["task1.py"],
        )


def test_parse_explicit_code_mappings_rejects_unknown_file():
    with pytest.raises(ValueError, match="文件名不匹配"):
        parse_explicit_code_mappings(
            [{"filename": "ghost.py", "question_number": 1}],
            filenames=["task1.py"],
        )
