# LangGraph 批改 agent 架构

> 状态：LangGraph 改造 P0–P5 已完成，P5 迁移已生成（`c177625dbb36`）。
> 批改由**本机后端进程内 LangGraph 图**完成，不再依赖外部编程助手、
> ACP 或 MCP（相关依赖与代码已整体移除）。模型双通道均已开放：官方
> OpenAI API 与 ChatGPT 订阅（OAuth 登录，见
> `docs/architecture/chatgpt-sub-spike.md`）。

## 1. 概览

```text
浏览器 React（评审页 AgentRunPanel）
    ↓ HTTP / SSE
FastAPI /api/agent/*
    ↓ 租约、心跳、崩溃恢复
独立 grading_worker（python -m app.grading_worker，并发 1）
    ↓ LangGraph 主图 + Postgres checkpointer（每 run 一个 thread）
plan → package_ingest →[缺 rubric]→ rubric_extract → supervisor_rubric → package_ingest
     →[含代码]→ code_test_prepare
          →[需生成]→ code_test_generate
          →[草稿待审批]→ code_test_ask → code_test_wait(interrupt) → code_test_apply
          →[已批准]→ code_test_run
          → teacher_ask → teacher_wait(interrupt) → teacher_apply
     → ensure_handles → prepare_briefs →(Send 逐项) score_item → assemble
     →[缺项]→ rescore_missing →(Send 重评) score_item
     → verify_prepare →[证据存疑]→ evidence_verify(独立模型) →(Send 重评) score_item
     → supervisor_finalize → finalize → END
（任一 fail-closed 分支 → fail → finalize）
    ↓ 每分支独立 Provider（订阅通道 turn-state 隔离）
AgentProvider.openai_api / chatgpt_sub
    ↓ 节点白名单工具；rubric/assessment 写工具仅 supervisor 节点执行，测试与检查点由受控图节点持久化
grading_package / grading_validation / grading_workflow / rubric（PostgreSQL）
```

## 1.1 图运行时约束

- **checkpoint 只存摘要**：评分包正文在 `grading_run_artifacts`，图状态只留
  `PackageDigest`（artifact_id/sha256/字符数/是否含代码）；节点用只读工具
  按需读取，避免把 200k 上下文塞进 checkpoint。
- **服务端组装 details**：`item_results` 由各评分分支提交，`assemble` 按
  rubric 顺序组装 `assessment_draft`；主管只补 `feedback/confidence`，
  不能改分数网格。
- **写权限三层**：`ToolSpec.writes` + 节点白名单（`graph/tool_guard.py`）+
  `GradingToolContext.allow_write`。评分/证据/rubric 提取分支永远拿不到写工具；
  模型发起的 rubric 与 assessment 写工具只开放给确定性 supervisor 节点
  （`supervisor_rubric` 落 rubric、`supervisor_finalize` 存整份评分）。测试套件、
  run 快照与教师检查点由代码测试和检查点图节点按服务端规则持久化。
- **教师门是结构节点**：`teacher_ask` 写库，`teacher_wait` 只 `interrupt()`，
  `teacher_apply` 校验答复；resume 从 wait 重跑，不会重复写 checkpoint。
  检查点是图节点而非模型工具，模型不再持有 `request_teacher_confirmation`。
- **分支隔离**：`ProviderPool` 为每个 `item:*` 分支创建独立 provider；
  订阅并发默认 2（`ConcurrencyGate` 排队而非失败），官方 3。
- **预算**：`RunBudget` 累计 token，超限 fail-closed；`rescore` 最多 1 轮、
  证据核查最多 2 轮，仍失败给出可读缺项/证据错误。
- **fail-closed**：rubric > 40 项、单项 brief 超 6000 字、模型未提交评分、
  证据无法定位且修正失败、rubric 提取校验失败，均失败并写 `error_message`，
  不静默记 0 分。

- **无外部协议进程**：评分不再外包给编程助手或 codex-acp；工具调用由
  `app/agent/tools/` 在进程内执行。application 层由
  `grading_package.py` 管理评分包、上下文与句柄，`grading_validation.py` 统一
  评分与证据校验，`grading_workflow.py` 负责事务性写入、锁序和幂等回执。
  `graph/node_runtime.py` 统一节点的模型网关构造、事件落库与预算内工具循环。
- **模型双通道**：`openai_api`（官方 Responses API）与 `chatgpt_sub`（ChatGPT
  订阅，OAuth PKCE 登录，复用同一套工具契约）；两条通道都由
  `AgentProvider.create_turn` 抽象，runner 按 run 的 `provider` 构造。
- **提示词唯一来源**：`app/agent/prompts/`（`supervisor.md` / `item_scorer.md` /
  `evidence.md` / `rubric.md` / `review.md`），每个接入图的提示词只描述其节点的
  操作步骤并**显式委派评分规则**——评分规则的唯一权威是评分包内的
  `grading_policy.requirements`。

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

## 3. 工具契约（进程内）

模型可见入参见 `app/agent/tools/`；`handle`/`submission_id`/`run_id` 一律由
服务端注入，模型不可见。节点只拿到白名单子集（`graph/tool_guard.py`）：

| 工具 | 拿到它的节点 | 说明 |
| --- | --- | --- |
| `list_sections` / `read_package_slice` / `search_package` | 评分/证据/rubric 提取分支 | 评分包只读检索，全文在 `grading_run_artifacts` |
| `read_code_lines` | 评分/证据分支 | 学生代码按行读取（含代码作业） |
| `read_rubric_item` | 评分/证据分支 | 单项 rubric 与满分 |
| `read_question_ocr` / `read_teacher_answer` | 评分/证据/rubric 提取分支 | 题干 OCR 与教师检查点答复 |
| `submit_item_score` | `score_item` | 提交单项评分与 `evidence_refs`（服务端校验） |
| `submit_evidence_fix` | `evidence_verify` | 只提交修正后的 `evidence_refs`，不改分数 |
| `submit_question_rubric` | `rubric_extract` | 提交从题目 OCR 提取的 rubric（服务端逐条校验） |
| `save_question_rubric` | `supervisor_rubric` | 确定性落库提取快照（句柄一次性） |
| `save_assessment` | `supervisor_finalize` | 保存整体 `feedback`/`confidence`；`request_id` 由服务端生成 |

- **rubric 提取在图内闭环**：题目只有 OCR、没有可信 rubric 时，`package_ingest`
  不再 fail-closed，而是路由到 `rubric_extract`（只读题目 OCR，交
  `submit_question_rubric`）→ `supervisor_rubric` 调用
  `save_question_rubric` 落 `Question.extracted_rubric` 快照 → 回到
  `package_ingest` 换新工件与评分句柄；提取结果按 `resolve_submission_rubric`
  优先级复用，OCR 变化即失效。
- **独立证据核查**：`verify_prepare` 预检不通过时，`evidence_verify` 作为独立
  模型节点（专用提示词与只读工具）重新定位证据并交 `submit_evidence_fix`，
  复核结论作为提示追加进该批次的评分说明，再由 `score_item` 重评（最多 2 轮）。
- **幂等**：`request_id = uuid5(run_id + payload_hash)` 服务端生成，同一
  payload 的重放返回原回执（`grading_assessment_receipts` 表语义与旧回执一致）。
- **证据回环**：`submit_item_score` 的证据校验失败（422 语义）会以工具错误
  回给该分支模型修正，而不是终结 run。
- **句柄**：评分包句柄与 rubric 提取句柄持久化于 `grading_handles`，**TTL 24h**
  （含代码检查点可跨夜驻留），绑定 revision 与 context hash。

## 4. 检查点与续跑

含代码作业在评分前先经过代码测试工作流：没有已批准版本时，测试生成节点只读取题目 OCR 与可信 rubric，教师预览并批准后，worker 在 macOS Seatbelt 中运行测试；Notebook 不自动执行。完整流程见[代码测试说明](code-testing.md)。随后再由**图节点**（不是模型工具）触发代码与报告一致性检查点：

1. `teacher_ask` 把 run 落为 `waiting_for_teacher` 并写 `checkpoint_request_id`，
   **释放租约**；`teacher_wait` 只调用 `interrupt()`，续跑状态由 checkpointer
   按 thread 保存（`grading_runs.agent_state` 为遗留列，不再写入）；
2. 教师在评审页看到检查点表单（可先在本地运行学生代码后核对，或直接确认；
   选择"不一致"必须填写说明），答复经 `POST /api/agent/runs/{id}/reply` 落库；
3. `POST /reply` 触发 resume，从 `teacher_wait` 重跑：`teacher_apply` 校验答复
   属于当前 `checkpoint_request_id` 且 revision/context hash 未变，不匹配即失败
   并提示重新发起；
4. 答复后回到 `ensure_handles` 重建评分句柄；若句柄已失效（24h 过期），
   服务端重新打开评分包，run 不需要重建。

## 5. 模型通道配置

设置页（`AgentProviderSettings`）与开始批改面板共享 `GET /api/agent/providers`：

- **官方 API 通道**：`api_key`（**只写不读**，响应仅回显 `configured` 与后
  四位）、`base_url`、`default_model`、`reasoning_effort`；连接测试
  （`POST /api/agent/providers/openai_api/test`）返回模型列表。
- **ChatGPT 订阅通道**：OAuth PKCE 登录（`127.0.0.1:1455` 回调），令牌存
  `system_config.chatgpt_tokens`（JSON，**不对外回显**）；`GET /providers`
  只暴露 `configured`/`available`/`plan`/`account_last4`/`expires_at`。
  `available` 要求 access_token 未过期或存在 refresh_token；过期且无法刷新
  时发起 run 返回 409，需重新登录。
  - 登录落库：回调线程换到令牌后**立即**调用注入的 sink 落库（重试 3 次），
    成功后才清内存副本；`/oauth/status` 只做补偿落库，**不依赖前端轮询**。
    回调带 generation，上一代迟到的回调不会覆盖新登录。
  - 令牌生命周期：`expires_at` 优先取 access_token 的 JWT `exp`（实测约
    10 天），到期前 60 秒自动用 `refresh_token` 轮换并回写；刷新做了
    **进程内单飞**、**跨进程租约**（`system_config.chatgpt_refresh_lease`，
    TTL 60s，赢家落库后才释放）与**失败回读**，正常情况无需重新登录。
    轮换后的令牌若落库永久失败会显式报错，不静默继续。
  - 模型清单来自 `GET /backend-api/codex/models?client_version=<codex 版本>`
    （`CODEX_CLIENT_VERSION` 可配）；**不得写死**，订阅侧模型与官方 API 不同。
    目录为空/畸形时返回空列表，不再伪造 fallback 模型。
  - 实测 effort 白名单 `low/medium/high/xhigh/max`（目录声明的 `ultra` 会被
    400 拒绝），前端在选中订阅通道时按此收敛选项。
  - 跨回合状态用响应头 `x-codex-turn-state` 承载（端点不返回 reasoning
    item），由网关随分支状态持久化并在续跑时回传。
  - 网络需要代理时配置 `MODEL_PROXY_URL`（如 `http://127.0.0.1:7890`），
    官方通道与订阅通道共用该设置。
- **默认通道**：`PUT /api/agent/providers/default` 单选；退出订阅登录时自动
  回落到官方通道。

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
POST   /api/agent/providers/chatgpt_sub/oauth/start   # 发起订阅登录（返回授权地址）
GET    /api/agent/providers/chatgpt_sub/oauth/status  # 轮询登录结果（回调已落库，这里仅补偿）
POST   /api/agent/providers/chatgpt_sub/oauth/logout  # 退出登录并清令牌
```

引擎事件：`message_delta` / `reasoning` / `tool_started` / `tool_finished` /
`checkpoint_requested` / `usage` / `turn_completed` / `notice` / `error`；
每条事件带 `agent` 标签（`supervisor` / `item:<rubric_item_id>`）供前端分组。
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

- 引擎约束（由 `graph/model_gateway.py` 的回合循环保证）：单回合超时 900s、
  取消轮询 2s、仅传输错误重试 2 次（退避 + `Retry-After`）、`max_rounds=40`；
  错误分类（401/403→auth_required、429→rate_limited、400+context→context_length、
  5xx/网络→retryable）由 `AgentProviderError` 承载。
- 工作区：`uploads/grading-workspaces/<run-id>/`，由 `cleanup_expired_workspaces`
  单独管理生命周期；run 崩溃后目录可安全重建。
- 终态 run 保留 7 天：worker 每小时执行 `purge_expired_runs`，删除
  `grading_run_artifacts` 行与对应 checkpointer thread（`adelete_thread`）。
- 状态写入全部走"锁内刷新 → 校验 claim token → 提交"，失效 worker 不得覆盖
  恢复后的记录。

## 8. 评分权威与提示词同步

- 评分规则的唯一权威是评分包内 `grading_policy.requirements`（由
  `build_grading_policy` 现算）；`prompts/supervisor.md` 只写提交步骤并显式委派。
- 工具名集合、节点白名单与核心术语由契约测试
  `tests/test_prompt_policy_sync.py` 双向锁定（提示词 × 工具子集矩阵，
  含 requirements 术语锚点），防止两边漂移。
- rubric 解析优先级不变：题目可信提取快照 → 配置的结构化 rubric → 内置默认。

## 9. 验证命令

```bash
cd backend && ruff check app tests && pytest -q
cd frontend && npx oxlint . && npx tsc -b --noEmit && npx vitest run && npm run build
cd backend && PYTHONPATH=. .venv/bin/python scripts/export_openapi.py ../frontend/src/api/openapi.json
cd frontend && npm run api:generate && npm run api:check
```

当前基线：后端 460 passed / 10 skipped；前端 132 tests / 17 文件。
