"""ACP 专用错误类型;映射规则沿用 ``app.core.errors``。

- ``AcpError``:所有 ACP 批改错误的基类,继承 ``ApplicationError``,
  API 层统一转 HTTP 500(子类覆盖状态码)。
- ``AcpLaunchError``:agent 进程启动失败(命令不存在、立即退出)。
- ``AcpProtocolError``:协议错误(乱码、非法 JSON-RPC、能力不满足)。
- ``AcpTimeoutError``:turn/会话操作超时。
- ``AcpAuthRequiredError``:agent 报告需要登录(映射 409,提示教师先登录)。
- ``AcpCancelledError``:run 被取消。
"""

from __future__ import annotations

from app.core.errors import ApplicationError, ConflictError


class AcpError(ApplicationError):
    """ACP 批改错误基类。"""


class AcpLaunchError(AcpError):
    pass


class AcpProtocolError(AcpError):
    pass


class AcpTimeoutError(AcpError):
    pass


class AcpAuthRequiredError(ConflictError):
    """agent 需要先完成登录;教师须在机器上手动认证。"""


class AcpCancelledError(AcpError):
    pass
