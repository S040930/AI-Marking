"""FastAPI 应用入口。

使用应用工厂模式创建 FastAPI 实例,模块级 `app` 便于 uvicorn 直接加载。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config import router as config_router
from app.api.health import router as health_router
from app.api.submissions import router as submissions_router
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期占位:startup / shutdown 钩子后续扩展。"""
    # startup
    yield
    # shutdown


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
    app.include_router(config_router, prefix="/api", tags=["config"])

    return app


app = create_app()
