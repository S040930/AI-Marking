"""Submission 接口测试:上传校验、列表、详情。"""

from app.models.submission import Submission, SubmissionStatus


def test_upload_non_pdf_returns_400(client):
    """POST /api/submissions 非 PDF 文件返回 400。"""
    response = client.post(
        "/api/submissions",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_get_nonexistent_submission_returns_404(client):
    """GET /api/submissions/999 不存在的 ID 返回 404。"""
    response = client.get("/api/submissions/999")
    assert response.status_code == 404


def test_get_submission_list_empty(client):
    """GET /api/submissions 空列表返回 200。"""
    response = client.get("/api/submissions")
    assert response.status_code == 200
    assert response.json() == []


def test_get_submission_detail_with_list_details(client, db_session):
    """回归测试:details 为数组类型时能正确序列化(Bug 1)。"""
    sub = Submission(
        original_filename="test.pdf",
        file_path="/tmp/test.pdf",
        status=SubmissionStatus.done,
        ocr_text="作业内容",
        score=85.0,
        feedback="整体不错",
        details=[
            {"criterion": "内容理解", "score": 25, "comment": "理解准确"},
            {"criterion": "论证分析", "score": 28, "comment": "逻辑清晰"},
        ],
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)

    response = client.get(f"/api/submissions/{sub.id}")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["details"], list)
    assert len(data["details"]) == 2
    assert data["details"][0]["criterion"] == "内容理解"
    assert "file_path" not in data  # Bug 2: file_path 不应泄露
