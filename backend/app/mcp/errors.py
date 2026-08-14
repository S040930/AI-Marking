"""MCP 适配器向模型暴露的可恢复错误类型。"""


class McpApiError(RuntimeError):
    """FastAPI MCP endpoint returned a recoverable error."""
