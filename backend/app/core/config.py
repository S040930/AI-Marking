"""应用配置模块。

使用 pydantic-settings 从环境变量 / .env 文件加载应用级配置。
LLM/OCR 的 API Key、endpoint、rubric 等业务配置已迁移到数据库
(由 ``app/services/config.py`` 与设置页面管理),不再在此声明。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 按文件位置解析,不依赖启动时的工作目录,保证
# 无论从哪个目录(如 CC Switch 启动 stdio)都能读到配置。
_BACKEND_DIR = Path(__file__).resolve().parents[2]

AI_MARKING_SERVICE = "ai-marking"
BACKEND_API_VERSION = "1"
MCP_API_VERSION = "8"


class Settings(BaseSettings):
    """应用配置类(仅保留应用级配置)。"""

    DATABASE_URL: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/ai_marking"
    )
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    # 文件上传目录
    UPLOAD_DIR: str = "./uploads"

    # uploads/ 清理策略:批改完成后 PDF 文本已入库,文件仅在复评场景需要
    # (当前无复评功能)。retention 天数后自动删除,保留 DB 记录。
    UPLOAD_RETENTION_DAYS: int = 7
    # 清理任务扫描间隔(秒)。默认 1 小时。
    CLEANUP_INTERVAL_SECONDS: int = 3600

    # PostgreSQL 持久化任务 worker
    TASK_CONCURRENCY: int = 4
    TASK_LEASE_SECONDS: int = 90
    TASK_MAX_ATTEMPTS: int = 3
    TASK_POLL_INTERVAL_SECONDS: float = 1.0

    # Deprecated compatibility setting; loopback MCP no longer authenticates
    # requests with a shared token.
    MCP_INTERNAL_TOKEN: str = ""
    MCP_MAX_GRADING_CONTEXT_CHARS: int = 200_000

    model_config = SettingsConfigDict(
        env_file=_BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()
