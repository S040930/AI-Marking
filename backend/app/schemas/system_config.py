"""SystemConfig Pydantic schemas。

`ConfigUpdate` 为 PUT 接口入参(字段全部可选,表示更新子集);
`ConfigOut` 为 GET 接口与 PUT 返回的完整结构。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.rubric import RubricDefinition


class ConfigUpdate(BaseModel):
    """配置更新入参(任一字段子集)。

    未知字段会被 Pydantic 拒绝(extra=forbid)，返回 422。
    """

    model_config = ConfigDict(extra="forbid")

    paddleocr_api_url: str | None = Field(
        default=None, description="PaddleOCR-VL 文档解析 API URL"
    )
    paddleocr_token: str | None = Field(
        default=None, description="PaddleOCR-VL Access Token(AI Studio 令牌)"
    )
    rubric_definition: RubricDefinition | None = Field(
        default=None,
        description="结构化评分标准；每个条目包含 criterion、max_score、details",
    )
    review_enabled: bool | None = Field(
        default=None,
        description="是否要求 MCP 客户端在保存建议前完成第二遍反向自检(默认开启)",
    )


class ConfigOut(BaseModel):
    """配置完整输出结构。"""

    model_config = ConfigDict(from_attributes=True)

    paddleocr_api_url: str = ""
    paddleocr_token: str = ""
    rubric_definition: RubricDefinition | None = None
    review_enabled: bool = True


class ConfigProfileOut(BaseModel):
    """配置项目元信息输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ConfigProfileCreate(BaseModel):
    """新建配置项目入参。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    copy_from_id: int | None = Field(
        default=None, description="非空时复制该项目的全部配置值"
    )


class ConfigProfileRename(BaseModel):
    """重命名配置项目入参。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
