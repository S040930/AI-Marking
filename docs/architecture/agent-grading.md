# 内嵌批改 agent 架构

> 状态：agent-refactor 阶段 0-4 已完成（分支 `feat/agent-refactor`）。
> 批改由**本机后端进程内自研 agent**（tool-calling 循环）完成，不再依赖
> 外部编程助手、ACP 或 MCP（相关依赖与代码已整体移除）。ChatGPT 订阅通道
> 为 spike 门控的占位能力，当前仅开放官方 OpenAI API 通道。

## 1. 概览

```text
浏览器 React（评审页 AgentRunPanel）
    ↓ HTTP / SSE
FastAPI /api/agent/*
    ↓ 租约、心跳、崩溃恢复
独立 grading_worker（python -m app.grading_worker，并发 1）
    ↓ 进程内 tool-calling 循环（engine）
AgentProvider.openai_api（httpx → OpenAI Responses API，store=false）
    ↓ 工具调用（进程内直调业务函数）
grading_workflow / rubric / 句柄与回执（PostgreSQL）
```

- **无外部协议进程**：评分不再外包给编程助手或 codex-acp；工具调用由
  `app/agent/tools/` 在进程内执行，业务函数复用原 MCP 时代沉淀的
  `app/application/grading_workflow.py`（评分包、rubric 校验、证据回环、
  回执幂等全部保留）。
- **模型双通道**：`openai_api`（官方 Responses API，已实现）与
  `chatgpt_sub`（ChatGPT 订阅适配器，未开放，设置页占位并提示账号风控风险）。
- **提示词唯一来源**：`app/agent/prompts/grading.md`（自旧 skill 裁剪），
  流程指令只描述操作步骤并**显式委派评分规则**——评分规则的唯一权威是
  评分包内的 `grading_policy.requirements`。

## 2. 运行生命周期

`GradingRun`（表 `grading_runs`）是独立于 `SubmissionStatus` 的 8 态状态机
（`app/domain/grading_run.py`）：

```text
queued → starting → running → completed / failed / cancelled
                       ↕
                waiting_for_teacher（教师检查点驻留）
                       ↕
                 cancelling → cancelled
```

| 状态 | 含义 |
| --- | --- |
| `queued` | 已创建，等待 worker 领取 |
| `starting` | worker 已领取，物化工作区、构建 provider 与工具 |
| `running` | 引擎循环执行中（含工具调用回合） |
| `waiting_for_teacher` | 检查点驻留，等待教师答复（释放租约，可跨夜） |
| `cancelling` | 已请求取消，等待 worker 在下一轮询点停止 |
| `completed` | 评分建议已保存（submission → `ready_for_review`） |
| `failed` | 达到重试上限或不可恢复错误 |
| `cancelled` | 已取消 |

教师侧入口在评审页 `/review/:id`：作业处于 `awaiting_grading` 时显示
`AgentRunPanel`——无活跃 run 时选择 通道/模型/思考强度 后点「开始批改」创建
run；有 run 时展示进度、运行中配置切换、取消与检查点答复。run 终态后
submission 状态由批改结果驱动（保存成功 → `ready_for_review`）。

## 3. 工具契约（进程内 5 工具）

模型可见入参见 `app/agent/tools/grading.py`；`handle`/`submission_id`/`run_id`
一律由服务端注入，模型不可见：

| 工具 | 模型可见入参 | 说明 |
| --- | --- | --- |
| `open_grading_package` | `continuation_token?` | 分页读取评分包；`needs_rubric=true` 时返回 rubric 提取句柄 |
| `save_question_rubric` | `status, items, total_max_score` | 题目 rubric 提取保存（服务端确定性校验） |
| `confirm_visual_review` | `verdict, note` | 含代码作业的教师一致性确认（mismatch 必填 note） |
| `save_assessment` | `assessment` | 评分建议保存；`request_id` 由服务端生成 |
| `request_teacher_confirmation` | `message` | 触发教师检查点（run 驻留 `waiting_for_teacher`） |

- **幂等**：`request_id = uuid5(run_id + payload_hash)` 服务端生成，同一
  payload 的重放返回原回执（`grading_assessment_receipts` 表语义与旧回执一致）。
- **证据回环**：`save_assessment` 的证据校验失败（422 语义）会以工具错误
  回给模型修正，而不是终结 run。
- **句柄**：评分包句柄与 rubric 提取句柄持久化于 `grading_handles`，**TTL 24h**
  （含代码检查点可跨夜驻留），绑定 revision 与 context hash。

## 4. 检查点与续跑

含代码作业在评分前由模型调用 `request_teacher_confirmation` 触发检查点：

1. 引擎捕获 `CheckpointRequested` → runner 将 run 落为 `waiting_for_teacher`，
   把 `agent_state`（对话项 + 句柄）持久化到 run 行，**释放租约**；
2. 教师在评审页看到检查点表单（可先在本地运行学生代码后核对，或直接确认；
   选择"不一致"必须填写说明），答复经 `POST /api/agent/runs/{id}/reply` 落库；
3. worker 扫描到已答复的检查点后按原锁序（Question → Submission → Run）续跑，
   续跑提示词显式要求"若句柄失效则重新 `open_grading_package`"；
4. 检查点超时（24h 句柄过期）后模型会自动重新打开评分包，run 不需要重建。

## 5. 模型通道配置

设置页（`AgentProviderSettings`）与开始批改面板共享 `GET /api/agent/providers`：

- **官方 API 通道**：`api_key`（**只写不读**，响应仅回显 `configured` 与后
  四位）、`base_url`、`default_model`、`reasoning_effort`；连接测试
  （`POST /api/agent/providers/openai_api/test`）返回模型列表。
- **ChatGPT 订阅通道**：未开放占位；实装前须先通过离线 spike（见
  `agent-refactor.md` 阶段 6）。
- **默认通道**：`PUT /api/agent/providers/default` 单选。

运行中热切换（`PATCH /api/agent/runs/{id}/configuration`，只接受
`model`/`reasoning_effort`）：

| run 状态 | 生效时机 |
| --- | --- |
| `queued` / 未启动 | 立即生效（`effective_at=current`） |
| `starting` / `running` / `waiting_for_teacher` | 排队到下一回合（`effective_at=next_turn`，响应 `pending_config` 非空） |
| 终态 | 409 |

provider 与会话绑定，运行中不可切换。

## 6. API 一览

```text
POST   /api/agent/runs                          # 创建 run（submission 须 awaiting_grading、无活跃 run、provider ready）
GET    /api/agent/runs/by-submission/{id}       # 活跃 run 详情（404 = 无活跃 run）
GET    /api/agent/runs/{id}                     # run 详情（含 applied/pending 配置）
PATCH  /api/agent/runs/{id}/configuration       # 切换 model/effort
GET    /api/agent/runs/{id}/events              # seq 回放
GET    /api/agent/runs/{id}/stream              # SSE（Last-Event-ID 续读，NOTIFY 频道 grading_run_events）
POST   /api/agent/runs/{id}/cancel              # 取消
POST   /api/agent/runs/{id}/reply               # 检查点答复（verdict + note）
GET    /api/agent/providers                     # 通道列表（密钥不回显）
PUT    /api/agent/providers/default             # 默认通道
PUT    /api/agent/providers/{name}/config       # 通道配置（null 不改、空串清除）
POST   /api/agent/providers/{name}/test         # 连接测试
```

引擎事件：`message_delta` / `reasoning` / `tool_started` / `tool_finished` /
`checkpoint_requested` / `usage` / `turn_completed` / `notice` / `error`。
`tool_finished` 只落摘要，不落整页评分包文本。

## 7. worker、恢复与资源边界

- `grading_worker`（独立进程，**并发 1**）按 90s 租约领取 run，心跳间隔为
  租约的 1/3；崩溃后运行中任务在租约到期时被恢复扫描重排。
- 恢复规则（过期租约、worker 关闭、检查点释放共用，`_recover_run`）：

| 恢复前 | 恢复后 |
| --- | --- |
| `cancelling` | `cancelled` |
| `waiting_for_teacher` 且有检查点 | 保持驻留（等待答复，不消耗重试） |
| `starting/running` 且建议已保存 | `completed` |
| 其余且 `attempts < 3` | `queued` |
| 其余且 `attempts >= 3` | `failed`（写入"达到重试上限"） |

- 引擎约束：单回合超时 900s、取消轮询 2s、仅传输错误重试 2 次
  （退避 + `Retry-After`）、`max_rounds=40`；错误分类
  （401/403→auth_required、429→rate_limited、400+context→context_length、
  5xx/网络→retryable）由 `AgentProviderError` 承载。
- 工作区：`uploads/grading-workspaces/<run-id>/`，由 `cleanup_expired_workspaces`
  单独管理生命周期；run 崩溃后目录可安全重建。
- 状态写入全部走"锁内刷新 → 校验 claim token → 提交"，失效 worker 不得覆盖
  恢复后的记录。

## 8. 评分权威与提示词同步

- 评分规则的唯一权威是评分包内 `grading_policy.requirements`（由
  `build_grading_policy` 现算）；`grading.md` 只写流程并显式委派。
- 工具名集合、委派锚句与核心术语由契约测试
  `tests/test_prompt_policy_sync.py` 双向锁定（grading.md ↔ ToolRegistry ↔
  requirements），防止两边漂移。
- rubric 解析优先级不变：题目可信提取快照 → 配置的结构化 rubric → 内置默认。

## 9. 验证命令

```bash
cd backend && ruff check app tests && pytest -q
cd frontend && npx oxlint . && npx tsc -b --noEmit && npx vitest run && npm run build
cd backend && PYTHONPATH=. .venv/bin/python scripts/export_openapi.py ../frontend/src/api/openapi.json
cd frontend && npm run api:generate && npm run api:check
```

当前基线：后端 398 passed / 10 skipped；前端 130 tests / 17 文件。
