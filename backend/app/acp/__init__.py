"""ACP 通用客户端内核。

分层约定与既有架构一致：
- ``domain``:纯枚举与状态迁移,无框架依赖;
- ``core``:错误类型;
- ``acp``:协议封装(启动 agent、会话驱动、事件归一化);
- ``application``:批改编排用例;
- ``api``:HTTP/SSE 适配。

ACP 评分使用独立 ``python -m app.acp_worker`` 进程承载 agent 子进程,
FastAPI 进程内不持有长生命周期子进程。
"""
