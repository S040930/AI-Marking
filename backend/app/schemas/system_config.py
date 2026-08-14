"""SystemConfig Pydantic schemas。

`ConfigUpdate` 为 PUT 接口入参(字段全部可选,表示更新子集);
`ConfigOut` 为 GET 接口与 PUT 返回的完整结构。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.rubric import RubricDefinition


class ConfigUpdate(BaseModel):
    """配置更新入参(任一字段子集)。

    未知字段会被 Pydantic 拒绝(extra=forbid),返回 422。
    """

    model_config = ConfigDict(extra="forbid")

    llm_api_key: str | None = Field(
        default=None, description="LLM API Key(OpenAI 兼容格式)"
    )
    llm_base_url: str | None = Field(
        default=None, description="LLM Base URL(OpenAI 兼容格式)"
    )
    llm_model: str | None = Field(default=None, description="LLM Model / Endpoint ID")
    review_llm_api_key: str | None = Field(
        default=None, description="审核 LLM API Key(用于 critic 复核节点)"
    )
    review_llm_base_url: str | None = Field(
        default=None, description="审核 LLM Base URL"
    )
    review_llm_model: str | None = Field(
        default=None, description="审核 LLM Model / Endpoint ID"
    )
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
    llm_user_prompt: str | None = Field(
        default=None,
        description="自定义 LLM 用户提示词模板(留空使用内置默认,支持 {rubric}/{output_format}/{ocr_text} 占位符)",
    )
    review_enabled: bool | None = Field(
        default=None,
        description="是否执行 critic 自动复核(默认开启;关闭时跳过 critic/revise 以节省 token)",
    )


class ConfigOut(BaseModel):
    """配置完整输出结构。"""

    model_config = ConfigDict(from_attributes=True)

    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    review_llm_api_key: str = ""
    review_llm_base_url: str = ""
    review_llm_model: str = ""
    paddleocr_api_url: str = ""
    paddleocr_token: str = ""
    rubric_definition: RubricDefinition | None = None
    llm_user_prompt: str = ""
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
