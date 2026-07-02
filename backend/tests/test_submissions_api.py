"""Submission 接口测试:上传校验、列表、详情。"""

from app.models.submission import Submission, SubmissionStatus


async def test_upload_non_pdf_returns_400(client):
    """POST /api/submissions 任一文件非 PDF 返回 400。"""
    response = await client.post(
        "/api/submissions",
        files=[
            ("file", ("test.txt", b"hello", "text/plain")),
            ("question_file", ("q.txt", b"hello", "text/plain")),
        ],
    )
    assert response.status_code == 400


async def test_get_nonexistent_submission_returns_404(client):
    """GET /api/submissions/999 不存在的 ID 返回 404。"""
    response = await client.get("/api/submissions/999")
    assert response.status_code == 404


async def test_get_submission_list_empty(client):
    """GET /api/submissions 空列表返回 200。"""
    response = await client.get("/api/submissions")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "skip": 0, "limit": 10}


async def test_get_submission_detail_with_list_details(client, db_session):
    """回归测试:details 为数组类型时能正确序列化(Bug 1)。"""
    sub = Submission(
        original_filename="test.pdf",
        file_path="/tmp/test.pdf",
        status=SubmissionStatus.reviewed,
        ocr_text="作业内容",
        score=85.0,
        feedback="整体不错",
        details=[
            {"criterion": "内容理解", "score": 25, "comment": "理解准确"},
            {"criterion": "论证分析", "score": 28, "comment": "逻辑清晰"},
        ],
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    response = await client.get(f"/api/submissions/{sub.id}")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["details"], list)
    assert len(data["details"]) == 2
    assert data["details"][0]["criterion"] == "内容理解"
    assert "file_path" not in data  # Bug 2: file_path 不应泄露


async def test_teacher_can_finalize_submission(client, db_session):
    """教师通过 /finalize 接口提交最终评分,状态变为 reviewed。"""
    sub = Submission(
        original_filename="review.pdf",
        file_path="/tmp/review.pdf",
        status=SubmissionStatus.ready_for_review,
        score=70,
        max_score=100,
        feedback="AI 反馈",
        details=[],
        ai_result={"score": 70, "max_score": 100},
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    response = await client.post(
        f"/api/submissions/{sub.id}/finalize",
        json={
            "reviewer_name": "Dr Chen",
            "score": 85,
            "max_score": 100,
            "feedback": "教师确认后的反馈",
            "details": [
                {
                    "criterion": "内容",
                    "score": 85,
                    "max_score": 100,
                    "comment": "内容完整",
                    "evidence": ["原文证据"],
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "reviewed"
    assert data["score"] == 85
    assert data["reviewed_by"] == "Dr Chen"
    assert data["ai_result"]["score"] == 70


async def test_finalize_rejects_already_reviewed(client, db_session):
    """对已 reviewed 的作业调用 finalize 返回 409。"""
    sub = Submission(
        original_filename="done.pdf",
        file_path="/tmp/done.pdf",
        status=SubmissionStatus.reviewed,
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    response = await client.post(
        f"/api/submissions/{sub.id}/finalize",
        json={
            "reviewer_name": "Dr Chen",
            "score": 85,
            "max_score": 100,
            "feedback": "反馈",
            "details": [
                {
                    "criterion": "内容",
                    "score": 85,
                    "max_score": 100,
                    "comment": "内容完整",
                    "evidence": [],
                }
            ],
        },
    )
    assert response.status_code == 409
