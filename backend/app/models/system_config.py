"""SystemConfig ORM 模型。

以 key-value 形式存储可配置项(API Key、endpoint、结构化 rubric 等),
便于通过页面修改并持久化到数据库,无需重启服务。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.db.base import Base


class SystemConfig(Base):
    """系统配置项(key-value 表)。

    每行存储一个配置项,``key`` 唯一,``value`` 为字符串
    (结构化 rubric 以 JSON 字符串保存)。
    """

    __tablename__ = "system_config"
    __table_args__ = (UniqueConstraint("key", name="uq_system_config_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        server_default=func.now(),
        onupdate=utc_now_naive,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<SystemConfig key={self.key!r}>"
