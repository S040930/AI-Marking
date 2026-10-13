# 自研批改 Agent 改造：去除 ACP/MCP，内置 agent + 双模型通道

> 状态：**阶段 0-4 已完成**（分支 `feat/agent-refactor`）；阶段 5（真实 key 端到端
> 验收与文档重写，文档部分已完成）与阶段 6/7（订阅 spike 门控）待做。
> 基线：`a092830`（v12 契约 + 并发修复已冻结，见文末「基线记录」）

## Context

现状：后端不内置 LLM，评分外包给外部编程助手（MCP stdio→HTTP）或 codex-acp（ACP 协议 + 独立 worker），前端与 `.agents/skills` 共同服务这两条通道。

目标（已定）：评分改为**后端自研 agent**（进程内 tool-calling 循环，不使用 ACP/MCP），模型双通道：

1. 官方 OpenAI API key（Responses API，**先落地**）；
2. ChatGPT 订阅适配器（opencode 式：OAuth PKCE 借 Codex 公开 client_id `app_EMoamEEZ73f0CkXaXp7hrann`，请求重写到 `chatgpt.com/backend-api/codex/responses` 并附 `ChatGPT-Account-Id`；**spike 门控、后落地**）。

事实基础（已验证）：

- 本机 `codex login status` = Logged in using ChatGPT；
- `backend/app/application/mcp_workflow.py`（约 1100 行）承载全部评分业务契约；
- ACP 运行骨架（状态机/租约/恢复/SSE）可整体复用；
- httpx 已有、`ConfigUpdate` 为 extra=forbid；
- 待删依赖 `mcp==2.0.0`、`agent-client-protocol==0.12.1`。

## 已拍板决策 / 默认决策

用户已确认：双通道都做、官方先行；作业对话功能 v1 删除；含代码检查点改“教师自愿确认”（保留服务端门控）；批改仅教师手动发起。

默认决策（审批本计划即确认，可随时反对）：

1. 枚举重命名 `awaiting_mcp`→`awaiting_grading`、`external_agent`→`agent`（backend/app + frontend/src 共 62 处引用，前后端同批次发布）；
2. 检查点驻留：`HANDLE_TTL_SECONDS` 30min → **24h**，且续跑提示词要求“句柄失效则重新 `open_grading_package`”；
3. `request_id` 从工具入参移除，服务端 `uuid5(NAMESPACE, run_id+payload_hash)` 生成——同 payload 天然幂等，与现回执表语义等价；
4. 订阅适配器 `originator` **自报应用名**；若端点拒绝自报身份，**不做伪装**，订阅轨回退为不做（届时再议）；
5. 阶段 2–4（后端切换、改名、前端）连续完成，期间 UI 暂不可用——单机自用可接受，不做双路由 feature flag；
6. 运行中允许切 model/effort（next turn 生效），不允许切 provider（会话绑定）；
7. 独立复核（`save_assessment_review`）v1 仅保留函数与数据字段，不接入 run；
8. 订阅轨若 spike 未过，设置页保留“未开放”占位，官方通道不受影响。

## 架构与分层

新分层（扩展 `backend/tests/test_architecture_boundaries.py`，新增 4 条 AST 规则）：

```
api → agent → application → services → core/domain
```

1. `app/agent/**` 不得 import `app.api`；
2. `app/agent/providers/**` 只许 import 标准库/httpx/`app.core`/`app.agent`（凭证构造注入，不得触 application/services/models/db）；
3. `app/application/**` 不得 import `app.agent`（唯一例外：import `app.models.grading_run`）；
4. `app/services/**` 不得 import `app.agent`。

### 模块布局

```
backend/app/agent/
  models.py     # ChatItem/TurnRequest/ProviderEvent/ToolSpec/ToolResult/EngineOutcome/错误分类
  provider.py   # AgentProvider 协议 + ProviderAuthStatus + ModelOption + AgentProviderError(kind/retryable/retry_after)
  engine.py     # AgentEngine.run(): tool-calling 循环、TextDeltaBuffer、取消(2s 缓存轮询)、
                #   单回合超时(900s)、仅传输错误重试(2 次,退避+retry_after)、max_rounds=40
  runner.py     # execute_run(run_id,...): 启动/续跑/检查点/终态(替代 orchestrator.execute_run)
  run_store.py  # 自 acp/runs.py 拆出的纯持久化(claim/续租/迁移/事件/恢复/重排队)
  checkpoints.py# 教师答复落库 + 续跑注入(answer_checkpoint 迁入)
  events.py     # 自 acp/events.py 裁剪:redact/截断/TextDeltaBuffer 保留,ACP 归一化删除
  workspace.py  # 自 acp/workspace.py 裁剪:去 skill.md/chat;根目录 uploads/grading-workspaces
  prompts/{grading.md, review.md, loader.py}
  providers/{__init__.py(工厂), sse.py, openai_api.py, chatgpt_sub.py, chatgpt_oauth.py}
  tools/{__init__.py(ToolRegistry), grading.py(4+1 工具)}

backend/app/domain/grading_run.py       # 8 态状态机(平移 acp/domain.py)
backend/app/models/grading_run.py       # GradingRun + GradingRunEvent ORM
backend/app/services/provider_config.py # system_config 键读写,密钥写不读回
backend/app/schemas/agent.py / api/agent.py / grading_worker.py(替代 acp_worker.py)
```

### 关键接口

- `AgentProvider`：`create_turn(TurnRequest) -> AsyncIterator[ProviderEvent]` / `list_models()` / `auth_status()` / `aclose()`；错误映射 401/403→auth_required、429→rate_limited（读 Retry-After）、400+context→context_length、5xx/网络→retryable；
- `TurnRequest(instructions, items, tools, model, reasoning_effort, max_output_tokens)`，无状态重放（官方 store=false）；
- 引擎事件：message_delta / reasoning / tool_started / tool_finished / checkpoint_requested / usage / turn_completed / notice / error；`tool_finished` 只落摘要，不落整页包文本；
- 工具失败→error 回给模型修正（对应现 422 证据回环）；`save_assessment` 成功→terminal，engine 结束；检查点工具抛 `CheckpointRequested` → runner 落 `waiting_for_teacher` + 持久化 `agent_state` → 释放 claim；`reply` 后按现恢复规则续跑（`runs.py:271-304` 语义）。

## 契约复用与删除映射

**整段复用（只改调用方）**：`mcp_workflow.py` 全部业务函数（评分包/分页/句柄、save 校验与回填、rubric 校验、检查点、复核、回执幂等）；`services/rubric.py`、`services/question_rubric.py`；`grading_handles`/`grading_assessment_receipts` 两表语义；`schemas/grading.py`（Mcp\*→Grading\*）的 extra=forbid 模型作工具入参。

**删除**：`app/mcp/` 5 文件、`api/mcp.py`、`api/acp.py`、`api/acp_chat.py`、`app/acp/{session,registry,model_catalog,security,errors,chat,chat_store}.py`、`acp_worker.py`（重写为 grading_worker）、`scripts/run-ai-marking-mcp`、`.agents/skills/ai-marking-grader/`（内容改写进 prompts/grading.md）、pyproject 的 mcp/ACP 依赖（httpx 已有，不新增）。

**mcp_workflow 改名时微调**：`AcpRun` import→`GradingRun`；`mcp_metadata.source="mcp"`→`"agent"`（读侧兼容旧键，前端同步）；文案“外部编程助手”→“AI 批改”；`MCP_MAX_GRADING_CONTEXT_CHARS`→`GRADING_MAX_CONTEXT_CHARS`；`save_mcp_assessment`→`save_agent_assessment`。

### 工具映射表（模型可见入参；handle/submission_id/run_id 服务端注入）

| 旧 MCP 工具 | 新工具 | 模型可见入参 |
|---|---|---|
| open_ai_marking_assignment | open_grading_package | continuation_token? |
| save_ai_marking_question_rubric | save_question_rubric | status, items, total_max_score |
| confirm_ai_marking_visual_review | confirm_visual_review | verdict, note（note 规则显式走 schema validator） |
| save_ai_marking_assessment | save_assessment | assessment（confidence/feedback/details；request_id 移除） |
| (elicitation) | 新增 request_teacher_confirmation | message |

## 数据库迁移（2 个 alembic revision）

**A. grading_runs 重构**：

- 清空非终态 `acp_runs` 及其事件；DROP `acp_chat_sessions`/`acp_chat_events`/`acp_agent_installations`；清空 `mcp_workflow_handles`（回执保留）；
- RENAME `acp_runs`→`grading_runs`、`acp_run_events`→`grading_run_events`（序列/索引/枚举类型同步改名）；
- DROP `agent_id`/`agent_snapshot`/`permission_mode`/`codex_config`/`applied_codex_config`/`pending_codex_config`/`acp_session_id`/`session_resumable`；
- ADD `provider`（必填）/`model`/`reasoning_effort`/`applied_config`/`pending_config`/`agent_state`/`usage`（JSON）。

**B. 契约改名（独立可回滚）**：

- `mcp_workflow_handles`→`grading_handles`；`mcp_assessment_receipts`→`grading_assessment_receipts`（+约束改名）；
- `submission_status` 值 `awaiting_mcp`→`awaiting_grading`；`grading_mode` 值 `external_agent`→`agent`；
- 迁移前停 API + worker；SQLite 测试库走 create_all 不受影响。

## API 契约（新前缀 `/api/agent`；删 `/api/acp`、`/api/mcp` 全部）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent/runs` | 建 run（submission 须 awaiting_grading、无活跃 run、provider ready；extra=forbid） |
| GET | `/api/agent/runs/by-submission/{id}` / `/{run_id}` | 详情（404=无活跃 run，前端沿用该语义） |
| PATCH | `/api/agent/runs/{id}/configuration` | 切 model/effort；响应 effective_at: current\|next_turn |
| GET | `/api/agent/runs/{id}/events` / `/stream` | seq 回放 / SSE（复用 api/acp.py SSE 全套：NOTIFY 频道 `grading_run_events`、Last-Event-ID、keepalive、`: done`、SSE 槽位） |
| POST | `/api/agent/runs/{id}/cancel` / `reply` | 取消 / 检查点答复（verdict+note） |
| GET | `/api/agent/providers` | 列表（不回显密钥，只 configured+last4） |
| PUT | `/api/agent/providers/{name}/config` | api_key/model/effort（null 不改，空串清除） |
| POST | `/api/agent/providers/{name}/test` | 连接测试 |
| POST/GET/POST | `/api/agent/providers/chatgpt_sub/oauth/{start,status,logout}` | 订阅登录（阶段 7）；PKCE + 127.0.0.1:1455 回调；token 刷新失败→needs_auth |

配置键（system_config，经 `services/provider_config.py`）：`provider_default`、`openai_api_key`、`openai_base_url`、`openai_default_model`、`openai_reasoning_effort`、`chatgpt_tokens`(JSON)、`chatgpt_default_model`。`/api/config` 不新增任何密钥字段（已确认 extra=forbid）。

## 前端

- **评审页**：删助手按钮/聊天面板/`review/acp-chat/*`/prefill 分支；`awaiting_grading`→新 `AgentRunPanel`（无 run：provider+model+effort 选择 +「开始批改」；有 run：进度/取消/检查点表单——自愿确认文案“可在本地运行学生代码后确认，或直接确认；不一致必填说明”；终态失败：错误+重新发起）；保留 `ManualReviewPanel`/`AssessmentReviewCard`/`FailedRecoveryPanel`；`McpAuditBanner`→`AgentAuditBanner`（`mcp_metadata` 兼容读）；
- **设置页**：删 `AcpAgentDirectory`（+提示）；新增 `AgentProviderSettings`（API key 卡、订阅登录卡+风险提示、默认通道单选；Zod 模式参照 `ConfigSettingsForm`/`lib/settingsForm.ts`）；
- **上传页**：删 `acp|mcp` 双选与 prefill；统一跳 `/review/{id}`；
- **文件**：删除 `api/acp.ts`、`api/acpChat.ts`、`AcpMarkingPanel(+test)`、`McpWaitingPanel`、`AcpRunProgress`、`gradingPrompt(+test)`、`acpRunStatus`、`mcpClients`；新增 `api/agent.ts`、`AgentRunPanel/AgentRunProgress`、`AgentProviderSettings`、`agentRunStatus`、对应测试；改 `queryKeys.ts`、`StatusBadge`、`submissionStatus/submissionTypes`、i18n 字典（`awaiting_grading`→“等待批改”等）；
- `generated.ts` 再生成：`python backend/scripts/export_openapi.py` + `npm run api:generate` + `npm run api:check`。

## 提示词

- `SKILL.md`→`prompts/grading.md`：删 frontmatter/模式判定/步骤 1-4/uploads/沙箱描述；保留步骤 5-8、needs_rubric、安全边界；工具名按上表替换；步骤 6 改为自愿确认（agent 不运行学生代码，含代码作业先 `request_teacher_confirmation` 等待答复——“本轮暂停是预期行为”）；request_id 由服务端生成；
- **评分规则唯一权威 = 评分包内 `build_grading_policy().requirements`**；grading.md 只写流程并显式委派，不复述规则；
- 契约测试 `test_prompt_policy_sync.py`：委派锚句、grading.md 工具名集合 == `ToolRegistry.specs()`、requirements 核心术语锚点一致、`.agents/skills` 已不存在；loader 用 `Path(__file__)` 相对读取 + 单测。

## 订阅 spike（阶段 6，离线脚本 `backend/scripts/spike_chatgpt_sub.py`）

- A 段（用本机 `~/.codex/auth.json` 现 token，不改生产代码）：A1 最小必填头/体集合；A2 **自定义 tool function-call 往返（核心验收）**；A3 `originator` 自报 vs codex_cli_rs 接受度；A4 usage/耗时/配额痕迹；A5 错误路径可用线索；A6 实测可用 model/effort 清单（不硬编码）；
- B 段：OAuth PKCE 全流程（:1455 回调、token 交换与刷新轮换）+ 连续 3-5 次调用风控观察；
- 产出 `docs/architecture/chatgpt-sub-spike.md`（脱敏证据 + 实装/放弃结论）；全过才进阶段 7。

## 实施阶段

基线检查（每阶段尾）：`ruff check backend`；`cd backend && pytest -q`；前端 `npm run lint && npm run test:run && npm run build`；契约变更加 `api:check`；迁移变更加 `alembic upgrade head && alembic current`。

0. **前置冻结**：提交当前未提交改动（按主题 2-3 个 commit，末个标记 checkpoint）；建分支 `feat/agent-refactor`；跑完整基线并记录；产出本文件。✅ 已完成（冻结 3 commit + checkpoint `19e4fbe`）
1. **内核（纯新增）**：`agent/{models,provider,engine,tools,prompts}`、`services/provider_config.py`；扩展边界测试；新增 `test_agent_engine`（脚本化 provider）/`test_grading_tools`（直调 handler）/`test_provider_config`/`test_prompt_policy_sync`。旧路径不动。✅ 已完成（`97709e9`，20 文件 +2851 行，45 新测试）
2. **运行骨架迁移与后端切换（最大）**：Migration A；domain/models/run_store/checkpoints/events/workspace/runner/grading_worker/api·schemas；挂新路由；删 acp 全包、acp_worker、api acp/acp_chat/mcp、app/mcp、启动脚本；events 频道改名；测试迁移（`test_acp_runs` 原样→`test_grading_runs`；删 orchestrator/session/registry/chat/fake_agent；新增 `test_agent_runner` 含 agent_state 恢复；`test_queue_postgres` 改写）。✅ 已完成（`b0323d9`，62 文件 +3434/−12448；Migration A 临时库全链验证；398 passed）
3. **契约改名与 MCP 残留清理**：Migration B；`mcp_workflow→grading_workflow`；schemas/models 改名；submission 元数据改名+兼容读；删 skills、`test_mcp_server`、`test_skill_materialization`；`test_marking/evidence/concurrency/cleanup` 改断言；pyproject 删依赖。✅ 已完成（`c0d0706`，63 文件；Migration B 临时库验证；402 passed）
4. **前端改造**（见前端节）：再生成契约；手工冒烟（三进程启动、404/500 归零）。✅ 已完成（`d393f64`，55 文件 +1469/−5942；130 tests；另删 grading-prompt 端点与"批改提示词"链）
5. **官方通道端到端验收**：真实 API key 跑验收清单——无代码作业全链路；含代码：检查点→答复（mismatch+note 分支）→续跑→confirm→save；证据 422 回环；取消/租约恢复（kill worker 重启）；运行中切 model；usage 显示。重写 docs（`acp-grading.md`→`agent-grading.md`，删 `mcp-grading.md`，更新 schema/review-page/PROJECT/README）。📝 文档重写已完成；**真实 key 验收待用户执行**（清单见下文）
6. **订阅 spike**（离线，见上）。
7. **订阅适配器实装（仅 spike 通过）**：`chatgpt_oauth`+`chatgpt_sub`+OAuth 端点+设置页订阅卡+httpx mock 测试；真实登录一次+一份作业批改；限流退避由 engine 已覆盖。

## 验证

每阶段基线检查（上）；阶段 5 手工验收清单（上）；spike 验收表（上）；收尾 `grep -rin "acp|mcp" backend/app frontend/src` 无残留、文档一致、README 三进程说明。

## 风险

1. 订阅通道灰色性质（端点/风控可变）——官方主线 + spike 门控 + 失败回退占位；
2. 检查点与句柄 TTL 错位——默认决策 2 覆盖，验收含隔日答复场景；
3. `store=false` 的 encrypted reasoning 回传在订阅端点的兼容性——spike A2 覆盖，不兼容则订阅轨降级；
4. token 成本与 5 小时窗吞吐——usage 落库/展示已预留，单作业数十万 input 量级需产品提示；
5. 迁移窗口期前端不可用——阶段 2-4 连续推进、同批次发布。

**关键文件**：`backend/app/application/mcp_workflow.py`（改名+微调，契约唯一源）；`backend/app/acp/runs.py`（拆分）；`backend/app/acp/orchestrator.py`（重写）；`backend/app/api/acp.py`（重写为 api/agent.py，SSE 模式源）；`frontend/src/pages/ReviewPage.tsx`（前端主改）。

## 基线记录（阶段 0 冻结产物）

- 分支：`feat/agent-refactor`（自 `5f360ae` 起）；
- 冻结提交：
  - `15d3c2b` fix(backend)：批改运行租约恢复统一策略与失效 worker 拒绝；
  - `0cf9251` feat(backend)：MCP API v12 证据规则强化与评分契约瘦身（迁移 `c3d5e7f9a1b2`）；
  - `a092830` feat(frontend)：v12 契约对接、状态刷新与提示词显式化（含文档）——checkpoint；
- 后端基线：`cd backend && .venv/bin/python -m pytest -q --basetemp=.pytest_tmp` → **486 passed, 10 skipped**；
- 前端基线：`npx oxlint .` → 0 警告 0 错误；`npx tsc -b --noEmit` → 通过；vitest 全量 21 文件 / 154 用例（此前记录）交由原生 shell 复核；
- 已知噪音（非失败）：`UploadQuestionsPage.test.tsx` 若干 `act(...)` stderr 警告；`app/acp/session.py:27` 的 `ClientSideConnection` DeprecationWarning（本阶段随 ACP 删除一并消失）。

## 实施记录（阶段 0-5）

| 阶段 | 提交 | 摘要 |
|---|---|---|
| 0 | `19e4fbe` | 冻结 3 commit（`15d3c2b`/`0cf9251`/`a092830`）+ 本计划文档 |
| 1 | `97709e9` | agent 内核纯新增（引擎/工具/提示词/providers/provider_config，45 新测试，4 条 AST 边界规则） |
| 2 | `b0323d9` | 骨架迁移+后端切换（Migration A `e5f7a9b1c3d4` 临时库全链验证；旧 ACP/MCP 全删；398 passed） |
| 3 | `c0d0706` | 契约改名（Migration B `f7a9b1c3d5e7`：表/约束/索引/序列/枚举值 RENAME；句柄 TTL 24h；`agent_metadata` 键+兼容读；删 skills/依赖；402 passed） |
| 4 | `d393f64` | 前端切换（AgentRunPanel/AgentProviderSettings/api/agent.ts；acp-chat/助手/prefill/grading-prompt 全链删除；130 tests；oxlint/tsc/build/api:check 通过） |
| 5 | （本文档更新） | 文档重写（agent-grading.md 新增、acp/mcp-grading.md 删除、schema/review-page/PROJECT/README/env/启动脚本/bootstrap 同步）+ 验收清单移交用户 |

### 阶段 5 真实 key 验收清单（需用户执行）

前置：`backend/.env` 指向自己的 PG（5555）；`./start.sh` 起四进程（前端/API/worker/grading_worker）；设置页配置官方 OpenAI API key。

1. **无代码作业全链路**：上传一份纯 PDF 作业 → 评审页「开始批改」→ 观察 SSE 转录（tool_started/finished、message_delta）→ 建议保存进入 `ready_for_review` → 人工复核 → 确认最终成绩 → 「评分来源」卡出现 `agent_metadata`。
2. **含代码作业 + 检查点**：上传含 ZIP 作业 → 开始批改 → run 进入 `waiting_for_teacher` → 在本地运行学生代码核对 → 依次验证两个分支：
   - `consistent`（无 note）→ 续跑 → 保存成功；
   - `mismatch` + note（必填校验）→ 续跑 → 保存成功。
3. **证据 422 回环**：在评分中构造一条"得分 >0 但未提供可定位证据"的建议时，确认工具错误回到模型并被修正（转录里可见 tool_finished 的错误摘要，run 不终结）。
4. **取消**：运行中点「取消批改」→ run 落 `cancelled`，页面回到可重新发起状态。
5. **租约恢复**：运行中 kill grading worker → 重启 → 租约过期后 run 重新排队并完成（或检查点驻留保持）。
6. **运行中切 model**：运行中切换 model/effort → 提示「下一回合生效」→ 转录中出现 `configuration_queued`；queued 状态下则立即生效。
7. **usage 显示**：run 完成后头部出现 `Token 用量: X in / Y out`。
8. **隔日检查点**（可选，验证 24h TTL）：检查点驻留后次日答复 → 续跑仍成功（句柄未过期）。

每项完成后记录结果与异常；失败项按 run 详情 `error_message` 与转录定位。
