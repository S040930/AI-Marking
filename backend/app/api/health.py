"""健康检查路由。"""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.config import AI_MARKING_SERVICE, BACKEND_API_VERSION

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    """返回服务健康状态与当前 UTC 时间戳。"""
    return {
        "status": "ok",
        "service": AI_MARKING_SERVICE,
        "api_version": BACKEND_API_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
