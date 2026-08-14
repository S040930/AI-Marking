"""SQLAlchemy 2.0 声明式基类。"""

from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""

    pass


# 导入模型确保元数据注册(避免循环导入,放末尾)
from app.models import (  # noqa: E402, F401
    background_job,
    config_profile,
    conversation,
    mcp_assessment_receipt,
    question,
    submission,
    submission_code_file,
    submission_code_input_file,
    system_config,
)


@event.listens_for(question.Question, "before_insert")
def _ensure_question_config_profile(
    mapper, connection, target
) -> None:  # noqa: ANN001 - mapper/connection 不参与逻辑
    """题目未指定配置项目时,自动绑定默认配置项目。

    仅在默认项目已存在时回填;没有默认项目时不创建(flush 期间禁止
    新增入库操作),由 API 层 ``create_question`` 保证先有默认项目再建行。
    确保任何绕过显式指定(如测试/脚本直接建行)的题目都有可用配置。
    """
    if target.config_profile_id is not None:
        return
    from app.services.config import get_default_profile

    db = Session.object_session(target)
    if db is not None:
        default = get_default_profile(db)
        if default is not None:
            target.config_profile_id = default.id
