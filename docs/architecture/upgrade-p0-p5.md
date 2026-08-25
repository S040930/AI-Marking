# 架构升级：P0-P5

> 历史说明：当前项目采用本机 loopback 部署，P3 的远端 `StorageBackend`
> 方案已废弃，文件直接使用本地 `uploads/` 目录。

针对前序架构评审发现的 6 个问题，按优先级给出可执行的改造方案。所有方案遵循项目既有"简洁实用、零中间件依赖"的取舍。

## 摘要

| 优先级 | 问题 | 方案 | 改动量 |
|---|---|---|---|
| P0 | async 路由 + 同步 Session 反模式 | 路由改回 `def` + chat 拆 Session 作用域 | 中 |
| P1 | 前端轮询 | SSE 推送（PG LISTEN/NOTIFY） | 中 |
| P2 | LLM 客户端缓存无失效 | 配置更新时主动清空缓存 | 小 |
| P3 | 文件存储本地耦合 | 引入 StorageBackend 抽象层 | 中 |
| P4 | 可观测性缺失 | Prometheus metrics + 死信管理 API | 中 |
| P5 | cleanup 全表加载保护集 | 反向用 DB 记录判断受保护文件 | 小 |

## P0：路由改回 def + chat 拆 Session

### 问题

- `submissions.py` 中 `list_submissions`、`get_submission_status`、`get_submission_pdf`、`batch_delete_submissions`、`list_conversations` 均为 `async def` 但仅调用同步 Session
- `chat_with_submission` 在 LLM 调用 30-60s 期间持续持有 DB 连接，10 个并发聊天即可耗尽 `pool_size=10` 连接池

### 实现

**变更 1：纯同步路由改回 `def`**

把无 `await` 的路由从 `async def` 改为 `def`，避免事件循环阻塞：

- [submissions.py](file:///Users/mac/Desktop/AI-Marking/backend/app/api/submissions.py)：`list_submissions` / `get_submission_status` / `get_submission_pdf` / `batch_delete_submissions` / `list_conversations`
- [questions.py](file:///Users/mac/Desktop/AI-Marking/backend/app/api/questions.py)：`list_questions` / `rename_question` / `delete_question`

保留 `async def` 的路由（含 `await`）：`create_submission` / `retry_submission` / `chat_with_submission` / `finalize_submission`

**变更 2：chat_with_submission 拆 Session 作用域**

把单次请求拆为三段，LLM 调用期间不持有 DB 连接：

```python
async def chat_with_submission(
    submission_id: int,
    payload: ChatRequest,
    session_factory: sessionmaker = Depends(get_session_factory),
):
    # 第一段:读数据,立即释放连接
    with session_factory() as db:
        sub = db.get(Submission, submission_id)
        # ...校验状态、读取 history、config、拷贝纯数据...

    # 第二段:LLM 调用,期间不持有任何 DB 连接
    chat_result = await chat_with_teacher(...)

    # 第三段:开新 Session,加锁重新校验状态后写入
    with session_factory() as db:
        sub = db.get(Submission, submission_id, with_for_update=True)
        # ...校验状态、写入 user_msg + assistant_msg、commit...
```

**变更 3：新增 `get_session_factory` 依赖**

[db/session.py](file:///Users/mac/Desktop/AI-Marking/backend/app/db/session.py) 新增 `get_session_factory()` 依赖，返回 `SessionLocal` 工厂。供 chat 路由在请求内多次开闭 Session 使用。测试时可通过 `app.dependency_overrides[get_session_factory]` 替换为 SQLite 工厂，与 `get_db` 的 override 保持一致。

### Why

消除 LLM 调用期间 DB 连接占用，10 并发聊天不再耗尽连接池。第三段加 `with_for_update` 防止并发 finalize 状态不一致。

## P1：SSE 推送（PG LISTEN/NOTIFY）

### 问题

- [useSubmissionStatus](file:///Users/mac/Desktop/AI-Marking/frontend/src/api/submissions.ts) 每 2s 轮询 `/status`
- 批改动辄 30s-2min，单次批改产生 15-60 次无效请求

### 实现

**新增 [events.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/events.py)**

- `notify_submission_status` / `notify_question_status`：在业务事务内 `EXECUTE NOTIFY`，事务提交后监听方才收到。非 PG 后端（SQLite 测试环境）为 no-op
- `submission_event_stream` / `question_event_stream`：async generator，供 `StreamingResponse` 直接消费
- LISTEN 使用独立 psycopg2 连接（非 SQLAlchemy 池），AUTOCOMMIT 隔离级别，避免 LISTEN 长连接占用业务连接池
- 启动时先推一次当前状态，避免客户端错过终态事件
- 每 15s 注释行 keepalive，避免 nginx/uvicorn 误判空闲断连

**新增 SSE 端点**

[submissions.py](file:///Users/mac/Desktop/AI-Marking/backend/app/api/submissions.py) 添加 `GET /submissions/{submission_id}/events`，返回 `StreamingResponse`，`media_type="text/event-stream"`，含 `X-Accel-Buffering: no` 头。

**业务侧 NOTIFY 调用**

- [marking.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/marking.py)：`_update_status` 内调用 `notify_submission_status`
- [question_ocr.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/question_ocr.py) / [question_replace.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/question_replace.py)：状态更新点加 `notify_question_status`
- [submissions.py](file:///Users/mac/Desktop/AI-Marking/backend/app/api/submissions.py)：`finalize_submission` 写入 `reviewed` 后 NOTIFY

**前端 [submissions.ts](file:///Users/mac/Desktop/AI-Marking/frontend/src/api/submissions.ts)**

- 新增 `useSubmissionEvents(id)` hook：用 `EventSource` 订阅 `/api/submissions/{id}/events`，收到事件后 invalidate status query；终态事件额外 invalidate 完整详情 query
- `useSubmissionStatus(id)` 退化为 30s 兜底轮询：`useSubmissionEvents(id)` + `useQuery` with `refetchInterval: isProcessing ? 30000 : false`

### Why

PG 自带 LISTEN/NOTIFY，零新增中间件。SSE 单连接复用，处理中阶段请求量从 30 次/min 降到 1 长连接。

## P2：LLM 客户端缓存失效

### 问题

- [_llm_clients](file:///Users/mac/Desktop/AI-Marking/backend/app/services/agent.py) 按 `(api_key, base_url)` 永久缓存
- `update_config` 更新配置后不清空缓存，旧 Key 客户端残留内存

### 实现

[agent.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/agent.py) 新增 `close_llm_clients_sync()`：仅清空 `_llm_clients` dict，不 `await client.close()`（`AsyncOpenAI` 由 GC 自动关闭连接池）。

[config.py](file:///Users/mac/Desktop/AI-Marking/backend/app/api/config.py) 的 `update_config` 成功后，若变更字段包含 `llm_api_key` / `llm_base_url` / `llm_model` / `review_llm_*`，调用 `close_llm_clients_sync()`。

### Why

配置更新后立即失效旧客户端，避免陈旧连接复用。一行 if 即可，最小改动。

## P3：StorageBackend 抽象层

### 问题

- `uploads/` 本地磁盘存储，多 API worker 必须共享同机
- `cleanup.py` 按 `st_mtime` 判断过期，文件被 touch 后失效
- `get_submission_pdf` / `get_question_pdf` 直接读本地路径，无法切换到对象存储

### 实现

**新增 [storage.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/storage.py)**

```python
class StorageBackend(Protocol):
    async def save(self, key: str, source_path: Path) -> str: ...
    async def read(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def resolve_url(self, key: str) -> str | None: ...
    def list_files(self) -> list[str]: ...
```

`LocalStorageBackend` 实现：
- `_full_path(key)` 通过 `relative_to` 校验防止路径穿越
- `save` 返回绝对路径字符串，兼容现有 `Submission.file_path` / `Question.file_path` 字段
- `resolve_url` 返回 `None`（本地存储走 API 中转）

模块级单例：`get_storage()` 返回已初始化的实例，`init_storage(base_dir)` 在 [main.py](file:///Users/mac/Desktop/AI-Marking/backend/app/main.py) lifespan 中调用。

### Why

引入接口不引入新依赖，本地实现保持现状。未来加 S3 实现只需新增 `S3StorageBackend` 类并切换 `init_storage`。

## P4：Prometheus metrics + 死信管理 API

### 问题

- 仅 `logging.basicConfig`，无 metrics/tracing
- `retry_or_dead_letter` 转死信后只能 SQL 查表
- 队列深度、LLM 失败率、OCR 重试次数等运维信号不可见

### 实现

**依赖**：`prometheus-fastapi-instrumentator==8.1.0` + `prometheus-client==0.22.1`

**新增 [metrics.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/metrics.py)**

```python
queue_depth = Gauge("marking_queue_depth", "后台任务队列深度", labelnames=("status",))
task_duration = Histogram("marking_task_duration_seconds", "后台任务执行时长", labelnames=("job_type",))
task_retries = Counter("marking_task_retries_total", "任务退避重试次数", labelnames=("job_type",))
dead_jobs = Counter("marking_dead_jobs_total", "进入死信的任务总数", labelnames=("job_type",))
lease_lost = Counter("marking_lease_lost_total", "任务租约丢失次数")
ocr_calls = Counter("ocr_calls_total", "PaddleOCR-VL 调用次数", labelnames=("result",))
llm_calls = Counter("llm_calls_total", "LLM 调用次数", labelnames=("node", "result"))
llm_duration = Histogram("llm_duration_seconds", "LLM 调用时长", labelnames=("node",))
```

**[main.py](file:///Users/mac/Desktop/AI-Marking/backend/app/main.py)** 注册 Instrumentator，自动埋点 HTTP 指标，`/metrics` 端点不进 schema。

**埋点位置**：
- [worker.py](file:///Users/mac/Desktop/AI-Marking/backend/app/worker.py)：`_refresh_queue_depth_gauge`（每次 claim 前后调用）、`_run_claimed` 计时与重试/死信计数、`_heartbeat` 续租失败时 `lease_lost.inc()`
- [agent.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/agent.py)：LLM 调用前后 `llm_calls.labels(node, result).inc()` + `llm_duration.labels(node).observe()`
- [ocr.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/ocr.py)：OCR 调用 `ocr_calls.labels(result).inc()`

**新增 [admin.py](file:///Users/mac/Desktop/AI-Marking/backend/app/api/admin.py)**

- `GET /api/admin/dead-jobs`：列出所有死信任务（按 `updated_at` 降序）
- `POST /api/admin/dead-jobs/{id}/retry`：重置 attempts 与 status，重新入队
- `DELETE /api/admin/dead-jobs/{id}`：永久删除死信记录

Pydantic 模型：`DeadJobOut` / `DeadJobRetryResponse` / `DeadJobDeleteResponse`

**前端管理页**：本次先不做，留待后续迭代。当前用 curl/SQL 验证。

### Why

prometheus-fastapi-instrumentator 自动埋点 HTTP 指标，手动埋点聚焦业务关键节点。死信管理 API 让运维可操作。

## P5：cleanup 反向查询优化

### 问题

- [periodic_cleanup_loop](file:///Users/mac/Desktop/AI-Marking/backend/app/services/cleanup.py) 每个周期 `SELECT Question.file_path, Question.replacement_file_path` 全表加载到内存
- 题目表增长后内存占用持续上升

### 实现

`periodic_cleanup_loop` 先从磁盘枚举已过保留期的文件，每 250 个候选路径一批，再分别查询题目、作业、代码和输入文件引用。只有整批查询都确认无引用的文件才会被删除；每批后结束只读事务，避免扫描大目录时长期持有数据库快照。

单文件的事务失败清理使用 DB 侧存在性查询，同样不加载全部引用。

### Why

`LIMIT 10000` 会把第 10001 条之后的真实引用当成无引用，属于数据安全问题而非单纯性能权衡。候选驱动的分批反查同时保证完整性与 O(250) 内存上限。

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| P0 chat 拆 Session 后第三段状态校验失败率上升 | 加 `with_for_update` 防并发；失败返回 409 让前端重试 |
| P1 SSE 连接在 nginx/uvicorn 超时断开 | 前端 EventSource 自动重连；兜底 30s 轮询；15s keepalive 注释行 |
| P1 PG NOTIFY 在长事务中延迟 | marking.py 已在 `_update_status` 内 commit 后才发 NOTIFY，无延迟 |
| P3 StorageBackend 接口设计不合理需返工 | 接口最小化（save/read/delete/exists/resolve_url/list_files），先跑通本地实现再扩展 |
| P4 prometheus 指标过多影响性能 | 仅埋关键节点；/metrics 端点不进 schema |

## 验证步骤

### 后端测试

```bash
cd backend && python -m pytest
# 期望:79 passed, 1 skipped (PostgreSQL 集成测试需 RUN_POSTGRES_TESTS=1)
```

### 前端测试

```bash
cd frontend && npm test -- --run
# 期望:72 passed
```

### P4 运行时验证

```bash
# 指标
curl http://localhost:8000/metrics | grep marking_

# 死信列表
curl http://localhost:8000/api/admin/dead-jobs

# 重试死信
curl -X POST http://localhost:8000/api/admin/dead-jobs/{id}/retry

# 删除死信
curl -X DELETE http://localhost:8000/api/admin/dead-jobs/{id}
```

### P1 SSE 验证

1. 上传作业后，在浏览器 Network 面板看到 `/events` SSE 长连接
2. 处理中阶段不再出现 2s 一次的 `/status` 请求（仅 30s 兜底）
3. worker 完成批改后，前端在 1s 内收到 SSE 事件并刷新
