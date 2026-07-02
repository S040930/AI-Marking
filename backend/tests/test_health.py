"""健康检查接口测试。"""


async def test_health_returns_ok(client):
    """GET /api/health 返回 200 且 status=ok。"""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
