"""FastAPI 应用入口。

使用应用工厂模式创建 FastAPI 实例,模块级 `app` 便于 uvicorn 直接加载。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.admin import router as admin_router
from app.api.config import router as config_router
from app.api.health import router as health_router
from app.api.mcp import router as mcp_router
from app.api.questions import router as questions_router
from app.api.submissions import router as submissions_router
from app.core.config import settings
from app.db.session import engine as _engine
from app.services.agent import close_llm_clients
from app.services.ocr import close_client as close_ocr_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。

    Startup:初始化本机应用资源。
    Shutdown:关闭 OCR/LLM 共享客户端并释放 DB 连接池资源。
    持久化任务与 uploads 清理由独立 ``python -m app.worker`` 进程负责。
    """
    try:
        yield
    finally:
        await close_ocr_client()
        await close_llm_clients()
        _engine.dispose()


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(
        title="AI 作业批改系统",
        description="SURF-2026-0031 后端 API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(submissions_router, prefix="/api", tags=["submissions"])
    app.include_router(questions_router, prefix="/api", tags=["questions"])
    app.include_router(config_router, prefix="/api", tags=["config"])
    app.include_router(admin_router, prefix="/api", tags=["admin"])
    app.include_router(mcp_router, prefix="/api")

    # Prometheus HTTP 指标自动埋点 + /metrics 端点暴露。
    # excluded_handlers 确保 /metrics 自身不计入指标,避免自引用。
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics"],
    ).instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
    )

    return app


app = create_app()
