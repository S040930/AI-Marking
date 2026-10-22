"""Errors shared by the trusted code execution pipeline."""


class CodeRunnerInfrastructureError(RuntimeError):
    """The native sandbox or its trusted supervisor failed; fail closed."""


class CodeRunnerCancelledError(RuntimeError):
    """The grading run was cancelled while student code was active."""
