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
# v11: 新增 GET /api/mcp/submissions/{id}/wait-ready 长轮询端点,
# MCP open 工具由固定 10s 轮询改为 NOTIFY 驱动的状态等待。
MCP_API_VERSION = "11"


class Settings(BaseSettings):
    """应用配置类(仅保留应用级配置)。"""

    # ⚠️ 默认值是本地开发口令，仅在本机 PostgreSQL 上使用；
    # 生产/共享环境必须在 .env 中改为强口令。
    DATABASE_URL: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/ai_marking"
    )
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    # 文件上传目录
    UPLOAD_DIR: str = "./uploads"

    # uploads/ 清理策略:retention 仅是无数据库引用孤儿文件的宽限期。
    # 题目、替换暂存、作业或代码记录仍引用的文件永不被定期清理。
    UPLOAD_RETENTION_DAYS: int = 7
    # 清理任务扫描间隔(秒)。默认 1 小时。
    CLEANUP_INTERVAL_SECONDS: int = 3600

    # PostgreSQL 持久化任务 worker
    TASK_CONCURRENCY: int = 2
    TASK_LEASE_SECONDS: int = 90
    TASK_MAX_ATTEMPTS: int = 3
    TASK_POLL_INTERVAL_SECONDS: float = 1.0

    # 外部编程助手 MCP 评分上下文总长度上限。
    MCP_MAX_GRADING_CONTEXT_CHARS: int = 200_000

    # 全站访问令牌。为空表示鉴权关闭，保持向后兼容；设置后所有 /api 路由
    # （除 /api/health、/api/mcp/health、/api/auth/login、/api/auth/logout 外）
    # 必须携带令牌。来源：浏览器会话 Cookie（登录页提交一次后自动携带）、
    # Authorization: Bearer / X-Access-Token（MCP/CLI/脚本）。可用
    # ``python -c "import secrets; print(secrets.token_urlsafe(32))"`` 生成。
    ACCESS_TOKEN: str = ""

    # SSE 实时事件连接上限。每条连接占用一条独立 PG LISTEN 连接，
    # 须远小于 PG max_connections 减去业务连接池与安全余量；
    # 单机本地工具实际峰值仅个位数连接。达到上限时新连接直接 503。
    MAX_SSE_CLIENTS: int = 16

    model_config = SettingsConfigDict(
        env_file=_BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()
