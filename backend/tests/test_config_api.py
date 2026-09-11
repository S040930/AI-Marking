"""系统配置接口测试。"""


async def test_get_config_returns_empty_defaults(client):
    """GET /api/config 在无配置时返回空字符串。"""
    response = await client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert data["paddleocr_api_url"] == ""
    assert data["paddleocr_token"] == ""
    assert data["rubric_definition"] is None
    assert data["review_enabled"] is True
    assert "llm_api_key" not in data
    assert "llm_user_prompt" not in data


async def test_put_config_updates_subset(client):
    """PUT /api/config 子集更新,其他字段保持不变。"""
    await client.put(
        "/api/config",
        json={"paddleocr_api_url": "https://ocr.example/jobs", "paddleocr_token": "t1"},
    )
    response = await client.put(
        "/api/config", json={"paddleocr_token": "t2"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["paddleocr_token"] == "t2"
    assert data["paddleocr_api_url"] == "https://ocr.example/jobs"  # 保持不变


async def test_put_config_updates_review_enabled(client):
    """review_enabled 布尔开关可更新。"""
    response = await client.put("/api/config", json={"review_enabled": False})
    assert response.status_code == 200
    assert response.json()["review_enabled"] is False


async def test_put_config_rejects_unknown_key(client):
    """PUT /api/config 未知 key 返回 422(extra=forbid)。"""
    response = await client.put("/api/config", json={"unknown_key": "value"})
    assert response.status_code == 422
    # 已删除的 LLM 配置 key 同样被拒绝
    response = await client.put("/api/config", json={"llm_api_key": "sk-001"})
    assert response.status_code == 422


async def test_put_config_empty_string_clears_value(client):
    """PUT /api/config 空字符串表示清空。"""
    await client.put("/api/config", json={"paddleocr_token": "sk-001"})
    response = await client.put("/api/config", json={"paddleocr_token": ""})
    assert response.status_code == 200
    assert response.json()["paddleocr_token"] == ""
