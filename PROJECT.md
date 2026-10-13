# AI 作业批改系统

## 项目简介

面向高校作业场景的智能批改平台。教师在网页题目库上传题目 PDF，在作业上传页提交学生报告 PDF 与代码文件（或含全部文件的 ZIP）；系统通过 OCR 提取文本，**后端内嵌自研 agent（进程内 tool-calling 循环）直接调用大模型完成评分**并保存可审计的建议，最终由教师在网页人工确认最终成绩。评分模型走官方 OpenAI API 通道（Responses API），ChatGPT 订阅通道为 spike 门控的占位能力。

## 技术栈

- **前端**：React 19 + Vite + TypeScript + Tailwind CSS v4 + shadcn/ui + React Router v7 + TanStack Query；内置简体中文/英文界面切换
- **后端**：FastAPI + Uvicorn + SQLAlchemy 2.0 同步 Session + Alembic + PostgreSQL
- **评分**：后端内嵌 agent（`app/agent/**`，httpx → OpenAI Responses API；无第三方 agent 协议依赖）
- **OCR**：PaddleOCR-VL（PDF → 结构化 Markdown）
- **任务队列**：PostgreSQL 持久化队列 + 两个独立 worker（OCR/清理 worker 与批改 worker）

## 目录结构

```
AI-Marking/
├── backend/          # FastAPI 后端（app/agent 内嵌批改 agent）
├── frontend/         # React 前端
├── scripts/          # 依赖引导、本地清理与存储迁移脚本
├── docs/             # 架构、数据与界面设计文档（见下文索引）
├── PROJECT.md        # 本文档：主索引
└── README.md         # 运行与开发说明
```

## 系统架构

当前系统是一个面向单实例部署的模块化单体：

```text
浏览器
  └─ React SPA
      ├─ React Router：页面路由
      ├─ TanStack Query：服务端状态、缓存与轮询
      ├─ Axios：/api 请求
  └─ EventSource：题目/作业状态与批改 run 事件 SSE 实时推送，30 秒兜底轮询
           ↓
FastAPI API（本机固定单进程）
  ├─ API：questions / submissions / config / admin / health / agent
  ├─ 应用用例层统一事务、锁顺序与状态迁移
  ├─ reviewed 最终成绩 → 按题目流式生成 Excel 分析快照
  └─ 本地 uploads 文件存储（仅 loopback 部署）
           ↓
PostgreSQL
  ├─ questions             ├─ grading_runs / grading_run_events（批改运行与事件）
  ├─ submissions           ├─ grading_handles（评分句柄与 rubric 提取句柄）
  ├─ background_jobs       ├─ grading_assessment_receipts（保存幂等回执）
  └─ system_config
           ↑
独立任务 worker（python -m app.worker）
  ├─ 租约、心跳、崩溃恢复与限次重试
  ├─ 首次题目 OCR / 题目新版 OCR / 作业 OCR / uploads 定期清理
  └─ 结构化任务日志、运行摘要与死信恢复
           ↑
独立批改 worker（python -m app.grading_worker，并发 1）
  ├─ 领取 grading_runs 租约并驱动内嵌 agent 引擎
  ├─ 引擎回合内直接调用模型与进程内工具（评分包/rubric/证据回环/保存）
  ├─ 检查点驻留（waiting_for_teacher）与教师答复续跑
  └─ 运行中配置热切换（model/effort 下一回合生效）
```

### 核心业务边界

- **题目域**：题目 PDF 上传（专门的批量上传页）、题目 OCR、题目复用、替换与删除。
- **批改域**：网页上传学生作业、失败原记录重试、作业 OCR、`awaiting_grading` 等待批改、教师发起 AI 批改、人工复核与最终确认。
- **模型通道域**：官方 OpenAI API 通道配置（密钥只写不读）、连接测试与默认通道；ChatGPT 订阅通道占位未开放。
- **配置域**：OCR 与 rubric 配置。
- **运维域**：运行摘要（`/api/admin/runtime`）与死信任务管理（`/api/admin/dead-jobs`），均受访问令牌保护。
- **文件生命周期**：题目和学生 PDF 按内容哈希共享；定期清理仅删除超过宽限期且无数据库引用的孤儿文件。PDF 预览接口同时支持 `GET` 与 `HEAD`。

### 运行模型

- API worker 只在事务内创建业务记录与 `background_jobs` 队列记录。
- 单独的 `python -m app.worker` 进程原子领取任务，并以租约和心跳恢复崩溃任务；批改任务由 `python -m app.grading_worker` 独立领取 `grading_runs`。
- 任务 worker 内部限制 OCR 并发，默认上限为 2；批改 worker 固定并发 1。
- OCR 请求内部仅重试短暂网络错误；最终失败后保留原因，由教师重新上传或重试。
- 前端通过 SSE 实时推送感知后台任务进度与批改 run 事件，30s 兜底轮询仅在 SSE 断开时触发；`awaiting_grading` 保持低频状态刷新直到批改保存建议。
- 本机优化部署固定一个 API 进程和两个 worker，`WORKERS>1` 拒绝启动。

## 架构升级 (P0-P5)

针对前序架构评审发现的 6 个问题已完成改造，详见 [docs/architecture/upgrade-p0-p5.md](docs/architecture/upgrade-p0-p5.md)。

| 优先级 | 问题 | 方案 |
|---|---|---|
| P0 | async 路由 + 同步 Session 反模式 | 纯同步路由改回 `def`；长耗时 LLM 调用由独立批改 worker 承载 |
| P1 | 前端 2s 轮询 | 基于 PG LISTEN/NOTIFY 的 SSE 推送（`/submissions/{id}/events`），轮询降级为 30s 兜底 |
| P2 | LLM 客户端缓存无失效 | 批改凭证走 provider 配置（密钥只写不读），配置项随通道保存 |
| P3 | 文件存储本地耦合 | 本地部署直接使用 uploads 目录，移除远端 StorageBackend 抽象 |
| P4 | 可观测性缺失 | 结构化日志 + 运行摘要 + 死信管理 API |
| P5 | cleanup 全表加载 / 截断引用 | 按 250 个过期文件候选分批反查 DB 引用，内存有界且不遗漏大表后续记录 |

## 后台任务失败语义

worker 依据异常类型区分「业务失败」与「系统失败」，采取不同策略：

- **业务失败**（`BusinessError`）：配置错误、文件不可识别等确定性错误。重试无意义，worker 立即将目标标记为终态（`failed` / `replacement_status=failed`）并删除任务行，**不进入队列退避重试**，也不产生死信。
- **系统失败**（其余 `Exception`）：网络瞬时抖动耗尽、数据库中断、进程崩溃等。worker 保留队列的租约 / 心跳 / 指数退避重试，达到 `TASK_MAX_ATTEMPTS` 上限后转入死信（`dead`），由运维介入。

OCR 在内部对超时 / 限流 / 5xx 进行最多 3 次短暂重试；网络重试耗尽仍属系统失败（可重试），而配置错误 / 4xx 认证 / 返回为空或结构异常则属业务失败（不重试）。批改 run 的重试语义独立：`MAX_ATTEMPTS=3`，传输错误由引擎内部重试（不消耗 run 次数），达到上限后 run 落 `failed` 并允许教师重新发起。

## 运行时与资源边界

- **OCR**：单次 HTTP 尝试超时 120s，可重试错误最多 3 次并指数退避；异步任务最长等待 10 分钟。PDF 单文件上限 50MB，单任务预计峰值 200–250MB，worker 默认并发 2，目标峰值低于 1GB。
- **批改 run**：单回合超时 900s，取消轮询 2s，`max_rounds=40`；租约 90s、心跳为租约 1/3；评分上下文硬上限 `GRADING_MAX_CONTEXT_CHARS=200000`（评分包每页 ≤40,000 字符）；句柄 TTL 24h。
- **uploads 清理**：时间为 O(F)（F 为上传目录文件数），只保留 250 个候选路径的内存批次；每批执行 4 次受路径集合约束的引用查询，不全表加载。批改工作区（`uploads/grading-workspaces`）由 `cleanup_expired_workspaces` 单独管理生命周期。
- **Excel 成绩导出**：按题目以 500 行游标批次读取已审阅记录，时间为 O(N + D)（N 为作业数、D 为评分项明细数）；XlsxWriter 使用 constant-memory 写入。

## 关键设计决策

1. **统一 PDF 处理链**：原生 PDF 流式接收并直接持久化，通过后端安全文件流与浏览器原生 iframe 预览。
2. **内嵌 agent 评分流程**：OCR → `awaiting_grading` → 教师在详情页发起批改 run → 引擎完成证据化评分 → `ready_for_review` → 教师表单式人工复核 → 最终评分确认。评分在独立 worker 进程内完成，不在 API 请求路径上执行。
3. **状态分离**：处理中使用轻量状态；`awaiting_grading` 与 `ready_for_review` 可读取详情并持续观察更新，只有 `reviewed`/`failed` 停止刷新。待复核详情每 30 秒刷新，人工草稿保留并绑定建议版本。
4. **配置入库**：PaddleOCR URL/token、结构化 rubric 与模型通道配置（含 API key）全部存入数据库，不依赖 `.env`；API key 只写不读，响应仅回显后四位。
5. **浅色极简设计系统**：以净白为底、Indigo 为主强调色，支持 `prefers-reduced-motion` 与 Skip Link 无障碍访问。
6. **前端语言偏好**：系统界面支持 `zh-CN` 与 `en-US`；首次访问跟随浏览器语言，用户切换后保存到浏览器 `localStorage`，用户业务内容保持原文。i18n 采用「**中文即 key**」的中文→英文单字典（`frontend/src/i18n/index.tsx`），规则详见 [docs/frontend/i18n.md](docs/frontend/i18n.md)。
7. **题目与作业解耦**：一个 `Question` 可关联多条 `Submission`，删除单条作业不会删除共享题目。
8. **数据库优先删除**：数据库事务提交成功后再清理文件，避免记录存在但文件提前丢失。
9. **题目新版暂存切换**：新版文档统一生成 PDF 后进入持久化 OCR 队列；成功后原子切换，失败时旧题目继续可用。
10. **最终评分单一入口**：批改 agent 只保存待确认建议；只有教师显式确认才写入 `reviewed`。`POST /submissions/{id}/finalize` 是写入最终评分的唯一入口，必填 `expected_revision`；锁内校验失败返回 409。
11. **测试显式建模题目关系**：测试不得自动为 `Submission` 注入默认题目；凡是创建作业记录，都必须显式创建 `Question` 并通过 `question` 或 `question_id` 关联，以避免绕过生产约束。
12. **批改发起单一入口**：批改 run 只能由教师在评审页 `AgentRunPanel` 发起（`POST /api/agent/runs`，须 `awaiting_grading` 且无活跃 run）；上传页与题目页不再有预填/复制提示词等旁路。含代码作业在 run 内触发教师检查点（自愿确认：可先在本地运行学生代码后核对）。
13. **运行与检查点的持久化所有权**：run 由独立 worker 以租约领取，检查点驻留时释放租约并可跨夜（句柄 TTL 24h）；恢复策略（过期租约、worker 关闭、检查点释放）统一为 `_recover_run`，失效 worker 不得覆盖恢复后的记录。运行中允许切 model/effort（下一回合生效），不允许切 provider（会话绑定）。
14. **本地 API 代理固定 IPv4**：Vite 将 `/api` 转发到 `127.0.0.1:8000`，避免 `localhost` 被优先解析为 IPv6 `::1` 而后端只监听 IPv4 时产生"网络连接异常"。
15. **报告—代码联动证据**：网页上传页支持一份 ZIP 作业（报告 PDF + 文件名以小题号结尾的代码 + 数据集，如 `task3.py`/`q3.py`），后端解包自动分类，数据集落库 `submission_code_input_files` 并随批改物化到隔离工作区；前端上传前仅读中央目录做只读预览。后端只保存源码与 SHA-256，不执行学生代码。
16. **统一 rubric 解析**：题目可信 OCR 提取快照（agent 提交、服务端确定性校验）优先，其次配置的结构化 rubric，最后是内置默认；网页与批改 agent 使用同一 Resolver、snapshot ID 和逐项校验。
17. **证据化评分**：agent 按权威 rubric 对每个评分项一次完成证据化评分（证据可定位、零分且无内容可用 `not_found` 免证据）；独立复核是保留的数据能力，教师审核才是最终安全阀。
18. **agent 工具契约收敛**：模型可见工具固定为 5 个（打开评分包、保存题目 rubric、确认代码一致性、保存评分、请求教师确认）；`request_id`、句柄、submission/run 身份一律服务端注入，模型不可见；评分包不包含后端执行信息，评分政策只由评分包提供。题目 rubric 由 agent 在 `needs_rubric` 分支从题目 OCR 提取，服务端以"引用必须是 OCR 原文子串且包含满分、各项满分之和等于总分、评分项不重复"确定性校验后写入权威快照。
19. **本地文件按内容寻址**：PDF 使用 SHA-256 作为共享实体路径，原始文件名只作为记录元数据保留；清理和题目替换删除前会查询题目、作业及替换引用，永不删除仍被引用的文件。开发依赖由 `scripts/clean-local` 和 `scripts/bootstrap` 按需清理、恢复（uv 锁文件为唯一事实源）。
20. **题目 ID 为文件名 slug**：`questions.id` 由上传文件名去扩展名生成稳定 slug 字符串（如 `DTS208TC_CW1_Paper`），不再使用自增整数；id 在创建时确定且不随替换/重试 OCR 变化，同名重复上传返回 409 拒绝，避免 ID 跳号与删除复用歧义。
21. **成绩导出只读快照**：`GET /api/submissions/export.xlsx` 只读取指定题目的 `reviewed` 最终成绩，以用户输入的及格线和当前界面语言生成三表 Excel；不读取 OCR/PDF/代码、不写数据库、不持久化导出文件，响应结束即删除临时文件。得分率和达标状态保留为工作簿公式，统计文字不调用模型。
22. **分层依赖单向向内**：`api → agent → application → services → core/domain`，由 `tests/test_architecture_boundaries.py` 强制（含 agent 四组 AST 规则：agent 不得引用 api；providers 只许标准库/httpx/core/agent；application/services 不得引用 agent）。错误类型统一在 `app/core/errors.py`；跨实体写事务必须位于 application 用例函数，API 层只做参数解析与序列化。
23. **评分来源可审计**：建议元数据记录通道、rubric 来源与快照、context hash、revision、request/payload hash 与生成时间（`agent_metadata` 键；v12 及更早历史数据为 `mcp_metadata`，读侧兼容）。教师页展示"AI 批改"来源与 rubric 来源徽标。

评分句柄按绑定版本校验，评分包缓存包含权威 rubric 快照；批改 run 的租约过期、优雅关闭与检查点释放共享恢复规则，详细状态与资源边界见 agent-grading 文档。

## 子文档索引

| 主题 | 路径 | 说明 |
|---|---|---|
| 模块化单体架构 | [docs/architecture/modular-monolith.md](docs/architecture/modular-monolith.md) | 分层边界、事务与锁、运行模型、SSE、契约及资源边界 |
| 内嵌批改 agent | [docs/architecture/agent-grading.md](docs/architecture/agent-grading.md) | run 状态机、5 工具契约、检查点跨夜、通道配置、API 一览与恢复规则 |
| agent 改造计划与记录 | [docs/architecture/agent-refactor.md](docs/architecture/agent-refactor.md) | 阶段 0-7 计划、默认决策、迁移与验收记录 |
| 历史架构升级 P0-P5 | [docs/architecture/upgrade-p0-p5.md](docs/architecture/upgrade-p0-p5.md) | 早期演进记录；当前决策以模块化单体文档为准 |
| 协同评分页设计 | [docs/frontend/review-page.md](docs/frontend/review-page.md) | 两栏布局、AgentRunPanel、表单式人工改分、finalize 确认流程 |
| 前端 i18n 约定 | [docs/frontend/i18n.md](docs/frontend/i18n.md) | 「中文即 key」的单字典机制、增删改规则、常见误判与最小验证路由 |
| 数据模型 | [docs/data/schema.md](docs/data/schema.md) | 数据表职责、关系、状态和文件生命周期 |

## 快速开始

- **首次使用**：先在「系统设置」配置 PaddleOCR 与模型通道（官方 OpenAI API key），再上传题目并等待 OCR 完成。
- **批改一份作业**：作业上传页提交报告 PDF（或含全部文件的 ZIP）→ 自动跳转评审页 → 选择模型与思考强度 → 「开始批改」→ 含代码作业按提示完成教师检查点 → 建议生成后人工复核并确认最终成绩。
- **本地开发**：配置好数据库后运行 `./start.sh`（自动校验依赖（uv）、迁移数据库、校验后端身份并启动前端、后端、任务 worker 与批改 worker，热重载）。前端严格使用 `5173`，后端严格使用 `8000`；端口被占用时直接报错，避免误连到错误应用。
- **生产部署**：运行 `./start.prod.sh`（单 API 进程、单任务 worker、单批改 worker、无 reload、前端生产构建预览）；`WORKERS` 仅允许为 1。

详细运行、配置与排错说明见 [README.md](README.md)。

## 改动域 affected-check 路由

每次改动完成后，先按 `git diff --name-only` 判断涉及的改动域，再只运行下表中对应的最小检查；所有检查都必须在**最终一次改动之后**重新运行。任一入口失败、依赖或环境缺失时停止继续扩大检查范围，先修复或明确记录阻塞原因，不用其他未列出的命令替代。

| 改动域 | 工作目录与现有入口 | 成功信号 | 停止边界 |
|---|---|---|---|
| 后端 `backend/` | `ruff check backend`；后端跨模块改动运行 `cd backend && pytest -q` | 命令退出码为 0；pytest 输出 `passed` | Ruff 或任一相关 pytest 失败时停止，修复后从最终改动重新运行对应命令 |
| 批改 agent `backend/app/agent/**` | `cd backend && pytest -q tests/test_agent_engine.py tests/test_grading_tools.py tests/test_agent_runner.py tests/test_prompt_policy_sync.py` | 全部通过（含提示词/工具契约同步测试） | 任一失败时停止；提示词契约失败必须同步 `prompts/grading.md` 与工具注册表 |
| 前端 `frontend/` | `cd frontend && npm run lint`；组件/交互改动追加 `npm run test:run`；构建或路由改动追加 `npm run build` | 各命令退出码为 0；build 完成且无 TypeScript/Vite 错误 | 任一 lint、test 或 build 失败时停止，不以 dev server 或人工点选结果替代 |
| 数据库迁移 `backend/` | `cd backend && .venv/bin/alembic upgrade head`，随后 `.venv/bin/alembic current` | upgrade 退出码为 0；current 能输出当前迁移版本 | 数据库不可连接、迁移失败或版本未更新时停止，不继续运行依赖新 schema 的服务检查 |
| API 契约变更 | `cd backend && PYTHONPATH=. .venv/bin/python scripts/export_openapi.py ../frontend/src/api/openapi.json` → `cd frontend && npm run api:generate && npm run api:check` | `api:check` 退出码为 0（生成物与提交一致） | 契约漂移或生成失败时停止 |
| 前端 i18n 字典（仅 `src/i18n/index.tsx` 条目增删，未触碰任何 `t()` 调用点与组件逻辑） | `cd frontend && npm run lint` | 退出码为 0 | 一旦改动涉及 `t()` 调用点或语言切换逻辑，即回归「前端 `frontend/`」行的完整检查；详见 [docs/frontend/i18n.md](docs/frontend/i18n.md) |

选择完最小集合后，记录实际命令、工作目录、最终修订和结果；跨域改动必须合并各涉及行，并在最后一次代码或迁移改动后重跑整组检查。
