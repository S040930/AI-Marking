"""应用配置模块。

使用 pydantic-settings 从环境变量 / .env 文件加载应用级配置。
LLM/OCR 的 API Key、endpoint、rubric 等业务配置已迁移到数据库
(由 ``app/services/config.py`` 与设置页面管理),不再在此声明。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类(仅保留应用级配置)。"""

    DATABASE_URL: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/ai_marking"
    )
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    # 文件上传目录
    UPLOAD_DIR: str = "./uploads"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()
