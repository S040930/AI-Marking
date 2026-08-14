from app.core.prompt import build_user_prompt


def test_prompt_requires_server_locked_rubric():
    prompt = build_user_prompt(
        "学生答案",
        rubric="[rubric_content] 内容 (100分): 评分细则\n总分:100分",
        question_text="题目细则",
    )
    assert "Rubric 选择优先级不可更改" in prompt
    assert "服务端已经解析并锁定的 rubric" in prompt
    assert "rubric_content" in prompt
    assert "学生作业原文" in prompt


def test_prompt_does_not_parse_rubric_from_question_or_legacy_cache():
    prompt = build_user_prompt(
        "学生答案",
        rubric="",
        question_text="请完成两道题。",
        cached_rubric="旧题目缓存细则",
    )
    assert "旧题目缓存细则" not in prompt
    assert "服务端未发现题目或配置 rubric" in prompt
