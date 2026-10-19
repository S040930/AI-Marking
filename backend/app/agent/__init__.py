"""内嵌批改 agent:进程内 tool-calling 循环,不依赖外部协议进程。

分层约束(见 tests/test_architecture_boundaries.py):
- ``app.agent`` 不得 import ``app.api``;
- ``app.agent.providers`` 只允许标准库/httpx/``app.core``/``app.agent``;
- ``app.application`` 与 ``app.services`` 不得 import ``app.agent``。
"""
