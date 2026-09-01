"""FastAPI 应用入口。

使用应用工厂模式创建 FastAPI 实例,模块级 `app` 便于 uvicorn 直接加载。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.auth import AccessTokenMiddleware
from app.api.auth import router as auth_router
from app.api.config import router as config_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.mcp import router as mcp_router
from app.api.questions import router as questions_router
from app.api.submissions import router as submissions_router
from app.core.config import settings
from app.core.errors import (
    ApplicationError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    ValidationError,
)
from app.db.session import engine as _engine
from app.services.ocr import close_client as close_ocr_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。

    Startup:初始化本机应用资源。
    Shutdown:关闭 OCR 共享客户端并释放 DB 连接池资源。
    持久化任务与 uploads 清理由独立 ``python -m app.worker`` 进程负责。
    """
    try:
        yield
    finally:
        await close_ocr_client()
        _engine.dispose()


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(
        title="AI 作业批改系统",
        description="AI 作业批改系统后端 API",
        version="0.1.0",
        lifespan=lifespan,
        # 关闭 /docs、/redoc 与 /openapi.json：这些端点不在 /api 前缀下，
        # 中间件无法覆盖，会绕过访问令牌（见安全审计 M-3）。
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 全站访问令牌：必须在 CORS 之后注册（中间件按注册顺序执行），
    # 401 响应才带正确的 CORS 头，前端能读取到错误信息。
    app.add_middleware(AccessTokenMiddleware)

    @app.exception_handler(ApplicationError)
    async def application_error_handler(request, exc: ApplicationError):  # noqa: ANN001
        status_code = 500
        if isinstance(exc, NotFoundError):
            status_code = 404
        elif isinstance(exc, ConflictError):
            status_code = 409
        elif isinstance(exc, ValidationError):
            status_code = 422
        elif isinstance(exc, PayloadTooLargeError):
            status_code = 413
        elif isinstance(exc, ServiceUnavailableError):
            status_code = 503
        return JSONResponse(status_code=status_code, content={"detail": exc.detail})

    app.include_router(auth_router, prefix="/api", tags=["auth"])
    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(submissions_router, prefix="/api", tags=["submissions"])
    app.include_router(questions_router, prefix="/api", tags=["questions"])
    app.include_router(config_router, prefix="/api", tags=["config"])
    app.include_router(events_router, prefix="/api")
    app.include_router(admin_router, prefix="/api", tags=["admin"])
    app.include_router(mcp_router, prefix="/api")

    return app


app = create_app()
