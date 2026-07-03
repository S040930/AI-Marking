"""SystemConfig Pydantic schemas。

`ConfigUpdate` 为 PUT 接口入参(字段全部可选,表示更新子集);
`ConfigOut` 为 GET 接口与 PUT 返回的完整结构(固定 6 字段)。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    paddleocr_api_url: str | None = Field(
        default=None, description="PaddleOCR-VL 文档解析 API URL"
    )
    paddleocr_token: str | None = Field(
        default=None, description="PaddleOCR-VL Access Token(AI Studio 令牌)"
    )
    rubric: str | None = Field(default=None, description="自定义评分标准(rubric)")
    llm_user_prompt: str | None = Field(
        default=None,
        description="自定义 LLM 用户提示词模板(留空使用内置默认,支持 {rubric}/{output_format}/{ocr_text} 占位符)",
    )
    operator_name: str | None = Field(
        default=None,
        max_length=100,
        description="操作人/审核教师姓名,用于 finalize 提交时作为 reviewer_name",
    )


class ConfigOut(BaseModel):
    """配置完整输出结构。"""

    model_config = ConfigDict(from_attributes=True)

    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    paddleocr_api_url: str = ""
    paddleocr_token: str = ""
    rubric: str = ""
    llm_user_prompt: str = ""
    operator_name: str = ""
    updated_at: datetime | None = None
