# 模块化单体架构

AI-Marking 面向单教师、单机 PostgreSQL、本地文件目录和 loopback 网络运行。
系统保持一个代码仓库、一个 API 进程和一个任务 worker，不引入微服务、Redis、
对象存储、通用 Repository 或依赖注入框架。

```text
React / TanStack Query
        │ HTTP / SSE
        ▼
API 与 MCP 适配层（薄适配：参数解析、序列化；写编排不在此层）
        ▼
应用用例层：事务、固定锁顺序、状态迁移、任务编排、错误定义
        ├── 纯领域：状态枚举、迁移规则
        └── 基础设施（services）：SQLAlchemy、PostgreSQL 队列、OCR、文件、事件
```

## 依赖方向（由 `tests/test_architecture_boundaries.py` 强制）

- 依赖只能向内：`api → application → services → core/domain`。
- `app/core` 是共享内核（config/time/errors），不依赖任何其他层；
  `ApplicationError` 族与 worker 重试分类 `BusinessError` 统一定义在
  `app/core/errors.py`。
- `app/services` 是纯基础设施，**不得 import `app.application`**；需要编排
  事务、锁序或状态迁移的逻辑属于 application 层（如 `marking`、
  `mcp_workflow`、`uploads`、`questions`、`submissions`、`dead_jobs`）。
- `app/api` 只做参数解析与序列化；跨实体写事务（删除、finalize、死信重试、
  MCP 评分保存等）必须位于 application 用例函数，单行纯 CRUD 允许留在路由。

## 边界与一致性

- `app/domain` 不依赖 FastAPI、SQLAlchemy、ORM 或路由。
- `app/application` 拥有跨实体写事务；上传采用“只读预检 → 无事务流式落盘 →
  重新锁定并提交”。异步路由通过线程池执行同步数据库阶段。
- 全局锁顺序是 `Question → Submission（ID 升序）→ WorkflowHandle/BackgroundJob`。
  MCP 令牌可先无锁解析目标，写入前必须按该顺序重新锁定并复核 revision/context。
- 状态字段通过应用层迁移网关写入；`reviewed` 不允许回退。
- 状态写入、后台任务写入和 PostgreSQL `NOTIFY` 属于同一事务；文件删除仅在提交后执行。
- 配置读取不使用进程内缓存，API 与 worker 每次从同一数据库读取少量配置行。

## 运行与事件

- `start.prod.sh` 固定一个 API 进程和一个任务 worker，`WORKERS>1` 直接拒绝。
- OCR 默认并发为 2。空队列轮询从 1 秒指数退避到 5 秒，领取任务后复位。
- 题目列表共享 `GET /api/events/questions`；事件固定为
  `{type:"question.changed", question_id, status, replacement_status}`。
  处理中仅保留 30 秒兜底轮询。
- `GET /api/admin/runtime` 返回队列、过期租约、题目和作业状态计数，不返回文件名、
  学生内容或密钥。死信重试会在同一事务内恢复业务目标和任务状态；源文件缺失返回 409。
- 不提供进程内 Prometheus 指标；使用含实体 ID、任务类型、revision、耗时和结果的
  结构化日志、运行摘要与死信 API。

## 数据、契约与资源边界

- PDF 按 SHA-256 内容寻址。`UPLOAD_RETENTION_DAYS` 仅控制无数据库引用孤儿文件的
  清理宽限期；引用文件永不由定期清理删除。
- 清理扫描时间为 O(F)，其中 F 为上传目录文件数；数据库反查和内存批次最多 250 个候选。
- 单个 PDF 上限 50MB。OCR 单任务最长 10 分钟；50MB 文件预计峰值内存约
  200–250MB，默认并发 2，目标 worker 峰值低于 1GB。
- `cd backend && python scripts/check_ocr_memory.py` 使用本地 MockTransport 保留
  两个并发 50MB 同步 OCR 请求的读取、base64 与 JSON 序列化路径，并以 1GB 为门禁。
- 公开 `/openapi.json` 保持关闭。`backend/scripts/export_openapi.py` 离线导出 schema，
  `openapi-typescript@7.13.0` 生成 `frontend/src/api/generated.ts`；生成后工作树必须无差异。
- PostgreSQL 专项测试只接受数据库名以 `_test` 结尾的 `TEST_DATABASE_URL`。
- CI 的 `backend/scripts/check_runtime_migration.py` 在 `_test` 数据库中回退一版、
  注入多个历史默认配置、升级并校验只保留最小 ID，随后再次回退和升级。
