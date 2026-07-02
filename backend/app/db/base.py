"""SQLAlchemy 2.0 声明式基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""

    pass


# 导入模型确保元数据注册(避免循环导入,放末尾)
from app.models import conversation, submission, system_config  # noqa: E402, F401
