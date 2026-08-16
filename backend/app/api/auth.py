"""全站访问令牌鉴权。

采用纯 ASGI 中间件而非 ``BaseHTTPMiddleware``：后者会缓冲流式响应
（SSE / PDF / 图片），破坏 ``text/event-stream`` 的实时推送。中间件
只在请求进入时校验令牌，响应原样透传，不触碰响应体。

安全模型：本工具定位为本地单用户，令牌只防御机会主义的网络/浏览器
访问（局域网、恶意网页、本机其他进程），不防能直接读取
``backend/.env`` 或浏览器 Cookie 存储的入侵者——那种级别本就
可以读走全部文件。

- ``ACCESS_TOKEN`` 为空 → 全部放行（向后兼容，未配置时行为不变）。
- 令牌来源三种，任一匹配即通过：
  ``Authorization: Bearer <t>``（MCP/CLI 客户端）/
  ``X-Access-Token: <t>``（脚本/测试）/
  ``ai_marking_access`` Cookie（浏览器会话，见下方 login/logout）。
  浏览器侧一律走 Cookie：令牌不进入 URL，避免泄露到浏览器历史、
  服务器日志与 Referer（安全审计 H-3/M-2）。
- 校验使用 ``secrets.compare_digest`` 常量时间比较。
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# 浏览器会话 Cookie 名。HttpOnly（JS 不可读）+ SameSite=Lax（防跨站携带），
# 不加 Secure：本机 http 部署；若改 HTTPS 部署需在响应中追加 Secure 属性。
COOKIE_NAME = "ai_marking_access"

# 探活端点保持公开：start.sh / start.prod.sh 与 MCP 客户端依赖
# /api/health、/api/mcp/health 做无鉴权探活。
# 注意：/api/auth/verify 不在豁免列表——它由中间件统一强制令牌，
# 前端令牌门正是靠它返回 401 来识别"鉴权已开启、需要输入令牌"。
# /api/auth/login 与 /api/auth/logout 必须公开：登录时还没有任何
# 凭据可校验；登出只是清除 Cookie，无令牌也能安全执行。
_PUBLIC_PATHS = {
    "/api/health",
    "/api/mcp/health",
    "/api/auth/login",
    "/api/auth/logout",
}


class LoginRequest(BaseModel):
    """登录请求体：待校验的访问令牌。"""

    token: str


def _token_matches(actual: str) -> bool:
    expected = settings.ACCESS_TOKEN
    if not expected or not actual:
        return False
    return secrets.compare_digest(
        actual.encode("utf-8"), expected.encode("utf-8")
    )


def _extract_token(request: Request) -> str:
    cookie_token = request.cookies.get(COOKIE_NAME, "")
    if cookie_token:
        return cookie_token.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[len("bearer "):].strip()
    return request.headers.get("X-Access-Token", "").strip()


class AccessTokenMiddleware:
    """校验除公开路径外所有 /api 请求的访问令牌。纯 ASGI，不缓冲响应。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not settings.ACCESS_TOKEN:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        if request.url.path in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        if not request.url.path.startswith("/api"):
            await self.app(scope, receive, send)
            return
        if _token_matches(_extract_token(request)):
            await self.app(scope, receive, send)
            return
        logger.info("访问令牌校验失败 [path=%s]", request.url.path)
        response = JSONResponse(
            status_code=401,
            content={"detail": "访问令牌无效或缺失"},
        )
        await response(scope, receive, send)


@router.get("/verify")
def verify_access_token() -> dict:
    """前端令牌门探测：鉴权关闭时恒 200；开启时由中间件强制令牌，
    无令牌/令牌错误返回 401，前端据此渲染登录门。"""
    return {"ok": True}


@router.post("/login")
def login(body: LoginRequest, response: Response) -> dict:
    """校验令牌并通过 httpOnly Cookie 建立浏览器会话。

    前端仅在登录门提交一次令牌；此后所有浏览器请求（含 SSE、
    PDF/图片下载等原生请求）自动携带 Cookie，令牌不再出现在 URL。
    鉴权关闭（ACCESS_TOKEN 为空）时恒 401——登录门在鉴权关闭时
    不会出现，此路径不会被调用。
    """
    token = body.token.strip()
    if not _token_matches(token):
        logger.info("登录失败:访问令牌无效")
        raise HTTPException(status_code=401, detail="访问令牌无效")
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    """清除会话 Cookie，前端据此回到登录门。公开端点：仅删除 Cookie，
    即使 Cookie 已失效也无副作用。"""
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"ok": True}
