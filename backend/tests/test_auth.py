"""全站访问令牌鉴权中间件测试。

通过 monkeypatch settings.ACCESS_TOKEN 开启鉴权，验证：
- 无令牌请求被 401
- Cookie（浏览器会话）/ Bearer / X-Access-Token 三种来源均可通过
- 登录接口校验令牌并设置 httpOnly Cookie；登出清除 Cookie
- 豁免路径（/api/health、/api/mcp/health、/api/auth/login、/api/auth/logout）
  无需令牌
- 非 /api 端点（/docs、/metrics、/openapi.json）已关闭
- ACCESS_TOKEN 为空时全部放行（默认向后兼容）
"""

from uuid import uuid4

import pytest

from app.api.auth import COOKIE_NAME
from app.core.config import settings

# 测试用随机令牌：避免静态扫描把固定字面量误判为硬编码凭据；
# 值仅用于验证鉴权中间件逻辑，不代表任何真实凭据。
TEST_ACCESS_TOKEN = f"test-token-{uuid4().hex}"


@pytest.mark.asyncio
async def test_auth_disabled_by_default_passes_all(client):
    """ACCESS_TOKEN 为空（默认）时，任意请求放行，不改变原有行为。"""
    assert settings.ACCESS_TOKEN == ""
    response = await client.get("/api/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_missing_token_returns_401(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    response = await client.get("/api/submissions?skip=0&limit=10")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_token_passes(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    response = await client.get(
        "/api/submissions?skip=0&limit=10",
        headers={"Authorization": f"Bearer {TEST_ACCESS_TOKEN}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_wrong_bearer_token_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    response = await client.get(
        "/api/submissions?skip=0&limit=10",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_x_access_token_header_passes(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    response = await client.get(
        "/api/submissions?skip=0&limit=10",
        headers={"X-Access-Token": TEST_ACCESS_TOKEN},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_cookie_token_passes(client, monkeypatch):
    """浏览器会话 Cookie 是网页端主凭据（httpOnly，前端 JS 不可读）。"""
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    client.cookies.set(COOKIE_NAME, TEST_ACCESS_TOKEN)
    response = await client.get("/api/submissions?skip=0&limit=10")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_cookie_wrong_token_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    client.cookies.set(COOKIE_NAME, "wrong")
    response = await client.get("/api/submissions?skip=0&limit=10")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_sets_cookie_and_grants_access(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    response = await client.post(
        "/api/auth/login", json={"token": TEST_ACCESS_TOKEN}
    )
    assert response.status_code == 200
    # 登录后同一客户端（Cookie jar 自动保存 Set-Cookie）即可访问受保护路由
    verified = await client.get("/api/auth/verify")
    assert verified.status_code == 200


@pytest.mark.asyncio
async def test_login_wrong_token_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    response = await client.post("/api/auth/login", json={"token": "wrong"})
    assert response.status_code == 401
    verified = await client.get("/api/auth/verify")
    assert verified.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_cookie(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    await client.post("/api/auth/login", json={"token": TEST_ACCESS_TOKEN})
    assert (await client.get("/api/auth/verify")).status_code == 200
    response = await client.post("/api/auth/logout")
    assert response.status_code == 200
    assert (await client.get("/api/auth/verify")).status_code == 401


@pytest.mark.asyncio
async def test_public_health_exempt(client, monkeypatch):
    """/api/health 供启动脚本与 MCP 客户端无鉴权探活，必须豁免。"""
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    response = await client.get("/api/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_public_mcp_health_exempt(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    response = await client.get("/api/mcp/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_auth_verify_requires_token_when_enabled(client, monkeypatch):
    """鉴权开启时 /api/auth/verify 同样强制令牌，无令牌 401——
    前端令牌门靠它区分"需登录"与"可直进"。"""
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    missing = await client.get("/api/auth/verify")
    assert missing.status_code == 401
    ok = await client.get(
        "/api/auth/verify", headers={"Authorization": f"Bearer {TEST_ACCESS_TOKEN}"}
    )
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}


@pytest.mark.asyncio
async def test_auth_verify_public_when_disabled(client, monkeypatch):
    """鉴权关闭时 /api/auth/verify 恒 200，前端直接放行。"""
    monkeypatch.setattr(settings, "ACCESS_TOKEN", "")
    response = await client.get("/api/auth/verify")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_metadata_endpoints_disabled(client, monkeypatch):
    """/docs、/openapi.json、/metrics 不在 /api 前缀下，中间件无法覆盖，
    因此直接关闭端点（M-3），避免绕过访问令牌暴露 API 全貌。"""
    monkeypatch.setattr(settings, "ACCESS_TOKEN", TEST_ACCESS_TOKEN)
    for path in ("/docs", "/openapi.json", "/metrics"):
        response = await client.get(path)
        assert response.status_code == 404, path
