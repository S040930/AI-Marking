"""Transport-independent errors raised by application and infrastructure code."""

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
