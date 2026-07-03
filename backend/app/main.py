"""FastAPI 应用入口。

使用应用工厂模式创建 FastAPI 实例,模块级 `app` 便于 uvicorn 直接加载。
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config import router as config_router
from app.api.health import router as health_router
from app.api.questions import router as questions_router
from app.api.submissions import router as submissions_router
from app.core.config import settings
from app.db.session import engine as _engine
from app.services.agent import close_llm_clients
from app.services.cleanup import periodic_cleanup_loop
from app.services.ocr import close_client as close_ocr_client

# 后台清理 task 引用保持。lifespan 启动时持有强引用,避免被 GC 回收。
_cleanup_task: asyncio.Task[None] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。

    Startup: 启动 uploads/ 定期清理后台任务。
    Shutdown: 取消清理任务,关闭 OCR/LLM 共享客户端,释放 DB 连接池资源。
    """
    global _cleanup_task
    _cleanup_task = asyncio.create_task(
        periodic_cleanup_loop(), name="uploads_cleanup"
    )
    try:
        yield
    finally:
        # shutdown:先取消清理任务,再关闭共享客户端与 DB 连接池
        if _cleanup_task is not None and not _cleanup_task.done():
            _cleanup_task.cancel()
            try:
                await _cleanup_task
            except asyncio.CancelledError:
                pass
        _cleanup_task = None
        await close_ocr_client()
        await close_llm_clients()
        await _engine.dispose()


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

    return app


app = create_app()
