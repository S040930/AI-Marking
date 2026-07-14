"""SystemConfig ORM 模型。

以 key-value 形式存储可配置项(API Key、endpoint、rubric 等),
便于通过页面修改并持久化到数据库,无需重启服务。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.db.base import Base


class SystemConfig(Base):
    """系统配置项(key-value 表)。

    每行存储一个配置项,``key`` 唯一,``value`` 为字符串
    (rubric 等长文本使用 ``Text`` 类型)。
    """

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
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
