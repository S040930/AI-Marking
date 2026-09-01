"""配置项目 ORM 模型。

每个配置项目是一套完整的 LLM/OCR/Rubric 配置,题目按需绑定其中一套。
``SystemConfig`` 中的配置行通过 ``profile_id`` 归属到项目。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.db.base import Base


class ConfigProfile(Base):
    """配置项目(profile)。

    每套配置对应一个项目,``name`` 唯一用于区分;``is_default`` 标记
    新题目未指定配置时回退的默认项目(仅允许一个为真)。
    """

    __tablename__ = "config_profiles"
    __table_args__ = (
        Index(
            "uq_config_profiles_single_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<ConfigProfile id={self.id} name={self.name!r}>"
