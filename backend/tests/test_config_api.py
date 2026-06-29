"""系统配置接口测试。"""


def test_get_config_returns_empty_defaults(client):
    """GET /api/config 在无配置时返回空字符串。"""
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert data["llm_api_key"] == ""
    assert data["llm_base_url"] == ""
    assert data["llm_model"] == ""
    assert data["paddleocr_api_url"] == ""
    assert data["paddleocr_token"] == ""
    assert data["rubric"] == ""
    assert data["llm_user_prompt"] == ""


def test_put_config_updates_subset(client):
    """PUT /api/config 子集更新,其他字段保持不变。"""
    # 先写入两个值
    client.put("/api/config", json={"llm_api_key": "sk-001", "llm_model": "gpt-4o"})
    # 再更新其中一个
    response = client.put("/api/config", json={"llm_api_key": "sk-002"})
    assert response.status_code == 200
    data = response.json()
    assert data["llm_api_key"] == "sk-002"
    assert data["llm_model"] == "gpt-4o"  # 保持不变


def test_put_config_rejects_unknown_key(client):
    """PUT /api/config 未知 key 返回 422(extra=forbid)。"""
    response = client.put("/api/config", json={"unknown_key": "value"})
    assert response.status_code == 422


def test_put_config_empty_string_clears_value(client):
    """PUT /api/config 空字符串表示清空。"""
    client.put("/api/config", json={"llm_api_key": "sk-001"})
    response = client.put("/api/config", json={"llm_api_key": ""})
    assert response.status_code == 200
    assert response.json()["llm_api_key"] == ""
