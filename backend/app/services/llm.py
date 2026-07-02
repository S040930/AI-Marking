"""LLM 批改默认配置常量(OpenAI 兼容协议)。

历史说明:本模块原含 ``mark_submission`` 单次调用函数,自 LangGraph Agent
([app.services.agent])接管批改流程后已不再使用,函数及 ``LLMError`` 已删除。
保留 ``DEFAULT_LLM_BASE_URL`` 与 ``DEFAULT_LLM_MODEL`` 供 Agent 回退使用。
"""

DEFAULT_LLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_LLM_MODEL = "doubao-pro-32k"
