"""错误类型共享内核。

传输无关的 ``ApplicationError`` 族由 API 适配层统一映射为 HTTP 状态码;
``BusinessError`` 是后台任务的重试分类:worker 据此决定「业务失败直接终态」
还是「系统失败进入队列退避重试/死信」。
"""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for errors mapped by transport adapters."""

    def __init__(self, detail) -> None:  # noqa: ANN001
        self.detail = detail
        super().__init__(str(detail))


class NotFoundError(ApplicationError):
    pass


class ConflictError(ApplicationError):
    pass


class ValidationError(ApplicationError):
    pass


class PayloadTooLargeError(ApplicationError):
    pass


class ServiceUnavailableError(ApplicationError):
    pass


class BusinessError(Exception):
    """永久业务失败:配置错误、文件不可识别、返回内容异常等。

    从 OCR / 批改流水线抛出处应明确使用本类型,以与瞬时网络错误
    (仍为 ``OCRError``)区分,避免被队列无意义地重复重试三次。
    """
