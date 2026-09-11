# AI 作业批改系统

## 项目简介

面向高校作业场景的智能批改平台。教师通过本机编程助手（Codex）提交学生报告 PDF 与代码文件，题目 PDF 在网页题目库上传；系统通过 OCR 提取文本，评分由编程助手通过 MCP 完成并保存可审计的建议，最终由教师在网页人工确认最终成绩。后端不再内置 LLM/Agent/Critic 评分链路。

## 技术栈

- **前端**：React 19 + Vite + TypeScript + Tailwind CSS v4 + shadcn/ui + React Router v7 + TanStack Query；内置简体中文/英文界面切换
- **后端**：FastAPI + Uvicorn + SQLAlchemy 2.0 同步 Session + Alembic + PostgreSQL
- **评分**：任意支持本地 STDIO MCP 的编程助手，评分由客户端完成
- **OCR**：PaddleOCR-VL（PDF → 结构化 Markdown）
- **任务队列**：PostgreSQL 持久化队列 + 独立 worker（仅负责 OCR 与文件清理）

## 目录结构

```
AI-Marking/
├── backend/          # FastAPI 后端
├── frontend/         # React 前端
├── scripts/          # 编程助手 MCP STDIO 启动器
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
  └─ EventSource：题目集合与单作业 SSE 实时推送，30 秒兜底轮询
           ↓
FastAPI API（本机固定单进程）
  ├─ API：questions / submissions / config / admin / health / mcp
  ├─ 应用用例层统一事务、锁顺序与状态迁移
  ├─ reviewed 最终成绩 → 按题目流式生成 Excel 分析快照
  └─ 本地 uploads 文件存储（仅 loopback 部署）
           ↓
PostgreSQL 持久化队列
  ├─ questions             ├─ PaddleOCR-VL
  ├─ submissions           └─ mcp_workflow_handles（评分句柄与 rubric 提取句柄）
  ├─ background_jobs
  └─ system_config
           ↑
独立任务 worker
  ├─ 租约、心跳、崩溃恢复与限次重试
  ├─ 并发上限（默认 2）
  ├─ 首次题目 OCR / 题目新版 OCR / 作业 OCR
  ├─ uploads/ 定期清理
  └─ 结构化任务日志、运行摘要与死信恢复
           ↑
本机编程助手 MCP（STDIO，任意兼容客户端）
  ├─ run-ai-marking-mcp → FastAPI /api/mcp（loopback），按可选 client 记录评分来源
  ├─ 编程助手内预检报告 PDF + 多语言小题文件，再提交并自动等待 OCR
  ├─ PostgreSQL TTL 评分包与续页句柄读取（MCP API v11）
  ├─ 编程助手评分建议、revision 乐观锁、evidence 校验
  └─ 不提供最终确认、删除、配置或密钥工具
```

ACP 批改与作业上下文对话是独立于 MCP 的本机 Codex 运行通道：

```text
浏览器 React
    ↓ HTTP/SSE
FastAPI /api/acp/* ── PostgreSQL acp_runs / acp_chat_sessions
    ↓ 租约、心跳、崩溃恢复
独立 app.acp_worker ── codex-acp（STDIO）
```

首期 Registry 只公开 `codex-acp`。模型、思考强度和速度选项由 ACP `config_option` 动态发现并缓存，期望配置、已应用配置和待应用配置分别持久化；连接测试就绪后即可运行，直接使用 Codex 原生 `workspace-write`、关闭网络，不提供 Full access，也不再维护应用层沙箱认证状态。

### 核心业务边界

- **题目域**：题目 PDF 上传（专门的批量上传页）、题目 OCR、题目复用、替换与删除。
- **批改域**：编程助手提交学生作业、失败原记录重试、作业 OCR、等待 MCP 评分、人工复核和状态流转；`awaiting_mcp` 作业亦可由首期唯一支持的 `codex-acp` 驱动。
- **审核域**：教师人工核对评分建议、表单式改分、确认最终结果。
- **配置域**：OCR、rubric 与 MCP 自检开关配置。
- **运维域**：运行摘要（`/api/admin/runtime`）与死信任务管理（`/api/admin/dead-jobs`），均受访问令牌保护。
- **文件生命周期**：题目和学生 PDF 按内容哈希共享；定期清理仅删除超过宽限期且无数据库引用的孤儿文件。PDF 预览接口同时支持 `GET` 与 `HEAD`。

### 运行模型

- API worker 只在事务内创建业务记录与 `background_jobs` 队列记录。
- 单独的 `python -m app.worker` 进程原子领取任务，并以租约和心跳恢复崩溃任务。
- 任务 worker 内部限制 OCR 并发，默认上限为 2；空闲轮询从 1 秒退避至 5 秒。
- OCR 请求内部仅重试短暂网络错误；最终失败后保留原因，由编程助手重新提交作业。
- 前端通过 SSE 实时推送感知后台任务进度，30s 兜底轮询仅在 SSE 断开时触发；`awaiting_mcp` 保持低频状态刷新直到编程助手保存建议。
- 本机优化部署固定一个 API 进程和一个任务 worker，`WORKERS>1` 拒绝启动。

## 架构升级 (P0-P5)

针对前序架构评审发现的 6 个问题已完成改造，详见 [docs/architecture/upgrade-p0-p5.md](docs/architecture/upgrade-p0-p5.md)。

| 优先级 | 问题 | 方案 |
|---|---|---|
| P0 | async 路由 + 同步 Session 反模式 | 纯同步路由改回 `def`；长耗时 LLM 调用随 Agent 链路一并移除 |
| P1 | 前端 2s 轮询 | 基于 PG LISTEN/NOTIFY 的 SSE 推送（`/submissions/{id}/events`），轮询降级为 30s 兜底 |
| P2 | LLM 客户端缓存无失效 | 后端不再持有 LLM 客户端，配置项移除 LLM 相关字段 |
| P3 | 文件存储本地耦合 | 本地部署直接使用 uploads 目录，移除远端 StorageBackend 抽象 |
| P4 | 可观测性缺失 | 结构化日志 + 运行摘要 + 死信管理 API |
| P5 | cleanup 全表加载 / 截断引用 | 按 250 个过期文件候选分批反查 DB 引用，内存有界且不遗漏大表后续记录 |

## 后台任务失败语义

worker 依据异常类型区分「业务失败」与「系统失败」，采取不同策略：

- **业务失败**（`BusinessError`）：配置错误、文件不可识别等确定性错误。重试无意义，worker 立即将目标标记为终态（`failed` / `replacement_status=failed`）并删除任务行，**不进入队列退避重试**，也不产生死信。
- **系统失败**（其余 `Exception`）：网络瞬时抖动耗尽、数据库中断、进程崩溃等。worker 保留队列的租约 / 心跳 / 指数退避重试，达到 `TASK_MAX_ATTEMPTS` 上限后转入死信（`dead`），由运维介入。

OCR 在内部对超时 / 限流 / 5xx 进行最多 3 次短暂重试；网络重试耗尽仍属系统失败（可重试），而配置错误 / 4xx 认证 / 返回为空或结构异常则属业务失败（不重试）。

## 运行时与资源边界

- **OCR**：单次 HTTP 尝试超时 120s，可重试错误最多 3 次并指数退避；异步任务最长等待 10 分钟。PDF 单文件上限 50MB，单任务预计峰值 200–250MB，worker 默认并发 2，目标峰值低于 1GB。
- **uploads 清理**：时间为 O(F)（F 为上传目录文件数），只保留 250 个候选路径的内存批次；每批执行 4 次受路径集合约束的引用查询，不全表加载。磁盘额外占用为 O(1)，超过 5 分钟时应通过日志分析文件数和查询耗时。
- **Excel 成绩导出**：按题目以 500 行游标批次读取已审阅记录，时间为 O(N + D)（N 为作业数、D 为评分项明细数）；XlsxWriter 使用 constant-memory 写入，内存只保留 N 个得分率和按评分项聚合的 O(C) 统计，临时磁盘为最终 `.xlsx` 文件大小。常规数千份作业预计在数秒内完成且使用数十 MB 以内额外内存；若超过 5 分钟或 1GB，应记录数据规模并分析查询、JSON 明细和压缩阶段。

## 关键设计决策

1. **统一 PDF 处理链**：原生 PDF 流式接收并直接持久化，通过后端安全文件流与浏览器原生 iframe 预览。
2. **MCP 评分流程**：OCR → `awaiting_mcp` → 编程助手保存建议 → `ready_for_review` → 教师表单式人工复核 → 最终评分确认。后端不内置 LLM/Agent/Critic。
3. **状态分离**：列表/处理中轮询使用轻量 `SubmissionStatusOut`，终态后再拉取完整 `SubmissionDetail`，避免传输 `ocr_text`/`assessment_suggestion` 等大字段。
4. **配置入库**：PaddleOCR URL/token、结构化 rubric 与 MCP 自检开关全部存入数据库，不依赖 `.env`。
5. **浅色极简设计系统**：以净白为底、Indigo 为主强调色，支持 `prefers-reduced-motion` 与 Skip Link 无障碍访问。
6. **前端语言偏好**：系统界面支持 `zh-CN` 与 `en-US`；首次访问跟随浏览器语言，用户切换后保存到浏览器 `localStorage`，用户业务内容保持原文。i18n 采用「**中文即 key**」的中文→英文单字典（`frontend/src/i18n/index.tsx`），规则详见 [docs/frontend/i18n.md](docs/frontend/i18n.md)。
7. **题目与作业解耦**：一个 `Question` 可关联多条 `Submission`，删除单条作业不会删除共享题目。
8. **数据库优先删除**：数据库事务提交成功后再清理文件，避免记录存在但文件提前丢失。
9. **题目新版暂存切换**：新版文档统一生成 PDF 后进入持久化 OCR 队列；成功后原子切换，失败时旧题目继续可用。
10. **最终评分单一入口**：编程助手只保存待确认建议；只有教师显式确认才写入 `reviewed`。`POST /submissions/{id}/finalize` 是写入最终评分的唯一入口。
11. **测试显式建模题目关系**：测试不得自动为 `Submission` 注入默认题目；凡是创建作业记录，都必须显式创建 `Question` 并通过 `question` 或 `question_id` 关联，以避免绕过生产约束。
12. **编程助手单一新建入口**：编程助手评分作业由本机 MCP 预检并提交，在同一任务内等待 OCR；网页端仅保留题目库上传，学生作业一律由编程助手提交。
13. **MCP 服务身份与版本校验**：健康响应同时声明 `service` 和 API 版本；启动脚本拒绝复用 8000 端口，诊断工具区分错误服务、旧进程、token 和数据库问题。
14. **本地 API 代理固定 IPv4**：Vite 将 `/api` 转发到 `127.0.0.1:8000`，避免 `localhost` 被优先解析为 IPv6 `::1` 而后端只监听 IPv4 时产生“网络连接异常”。外部评分记录通过 `mcp_metadata.client` 显示客户端配置中的名称，不保存未经验证的模型申报值。
15. **报告—代码联动证据**：编程助手可提交一份报告 PDF 和按小题映射的 Python/Notebook、R、Java、C/C++ 代码组；后端只保存源码与 SHA-256。代码由当前编程助手任务在临时目录中尝试运行，结果留在对话中；含代码作业评分前需取得教师人工一致性确认，教师仍是最终确认者。网页上传页也支持一份 ZIP 作业（报告 PDF + 文件名以小题号结尾的代码 + 数据集，如 `task3.py`/`q3.py`），后端解包自动分类，数据集落库 `submission_code_input_files` 并随批改物化到隔离工作区；前端上传前仅读中央目录做只读预览。
16. **统一 rubric 解析**：题目可信 OCR 提取快照（客户端提交、服务端确定性校验）优先，其次配置项目中的结构化 rubric，最后是内置默认；网页与编程助手 MCP 使用同一 Resolver、snapshot ID 和逐项校验。
17. **双遍精评**：ai-marking-grader 先按 rubric 建立证据账本并逐项评分，再独立反向复核证据、部分得分、宽严一致性和重复扣分；反馈提供可执行改进动作，保存前仍只生成教师待确认建议。
18. **MCP v10 协议收敛**：编程助手正常评分使用预检、提交、打开、待办列表、rubric 提取、人工确认与保存工具；不接受视觉比较输入、自由 rubric 文本、运行日志或代码产物。预检计划仅保留文件元数据并主动清理过期项，服务端评分句柄持久化于 PostgreSQL 并绑定文件/上下文，评分包不包含后端执行信息，评分政策只由评分包提供。题目 rubric 不再由后端 LLM 生成：`open_ai_marking_assignment` 返回 `needs_rubric` 时由客户端从题目 OCR 提取，服务端以「引用必须是 OCR 原文子串且包含满分、各项满分之和等于总分、评分项不重复」确定性校验后写入权威快照。
19. **本地文件按内容寻址**：PDF 使用 SHA-256 作为共享实体路径，原始文件名只作为记录元数据保留；清理和题目替换删除前会查询题目、作业及替换引用，永不删除仍被引用的文件。开发依赖由 `scripts/clean-local` 和 `scripts/bootstrap` 按需清理、恢复。
20. **题目 ID 为文件名 slug**：`questions.id` 由上传文件名去扩展名生成稳定 slug 字符串（如 `DTS208TC_CW1_Paper`），不再使用自增整数；id 在创建时确定且不随替换/重试 OCR 变化，同名重复上传返回 409 拒绝，避免 ID 跳号与删除复用歧义。
21. **成绩导出只读快照**：`GET /api/submissions/export.xlsx` 只读取指定题目的 `reviewed` 最终成绩，以用户输入的及格线和当前界面语言生成三表 Excel；不读取 OCR/PDF/代码、不写数据库、不持久化导出文件，响应结束即删除临时文件。得分率和达标状态保留为工作簿公式，统计文字不调用模型。
22. **分层依赖单向向内**：`api → application → services → core/domain`，由 `tests/test_architecture_boundaries.py` 强制。错误类型统一在 `app/core/errors.py`（`ApplicationError` 族映射 HTTP 状态码，`BusinessError` 驱动 worker 重试分类）；`services` 只做纯基础设施、不得引用 `application`；跨实体写事务（删除、finalize、死信重试、MCP 评分保存等）必须位于 application 用例函数，API 层只做参数解析与序列化。
23. **Codex ACP 配置动态化且可审计**：首期只允许 `codex-acp`；模型、思考强度与速度从 ACP `config_option` 动态获取，不硬编码模型列表。配置按期望/已应用/待应用三态入库，忙碌会话在安全点切换，失败恢复上一个已应用值并记录事件；连接测试就绪后直接启动 Codex 原生 `workspace-write`、关闭网络的沙箱，权限档位按运行时选择处理，不维护额外认证状态。

## 子文档索引

| 主题 | 路径 | 说明 |
|---|---|---|
| 模块化单体架构 | [docs/architecture/modular-monolith.md](docs/architecture/modular-monolith.md) | 分层边界、事务与锁、运行模型、SSE、契约及资源边界 |
| 历史架构升级 P0-P5 | [docs/architecture/upgrade-p0-p5.md](docs/architecture/upgrade-p0-p5.md) | 早期演进记录；当前决策以模块化单体文档为准 |
| 协同评分页设计 | [docs/frontend/review-page.md](docs/frontend/review-page.md) | 左右分栏布局、等待 MCP 面板、表单式人工改分、finalize 确认流程 |
| 前端 i18n 约定 | [docs/frontend/i18n.md](docs/frontend/i18n.md) | 「中文即 key」的单字典机制、增删改规则、常见误判与最小验证路由 |
| 编程助手 MCP 评分 | [docs/architecture/mcp-grading.md](docs/architecture/mcp-grading.md) | STDIO 连接、状态机、PDF+代码证据、工具契约、安全边界与排错 |
| Codex ACP 批改与对话 | [docs/architecture/acp-grading.md](docs/architecture/acp-grading.md) | Codex-only Registry、动态模型/思考/速度配置、持久 worker、会话安全点与 API 一览 |
| 编程助手评分 skill | [.agents/skills/ai-marking-grader/SKILL.md](.agents/skills/ai-marking-grader/SKILL.md) | 预检、提交、打开评分包、rubric 提取、双遍精评与教师复核；评分政策由服务端评分包提供 |
| 数据模型 | [docs/data/schema.md](docs/data/schema.md) | 数据表职责、关系、状态和文件生命周期 |

## 快速开始

- **编程助手首次接入**：在客户端 MCP 配置中填写 `scripts/run-ai-marking-mcp` 的绝对路径和可选客户端名称参数；代码只由当前编程助手任务在临时目录中尝试运行，后端不执行学生代码。
- **本地开发**：配置好数据库后运行 `./start.sh`（自动安装依赖、迁移数据库、校验后端身份并启动前后端与 worker，热重载）。前端严格使用 `5173`，后端严格使用 `8000`；端口被占用时直接报错，避免误连到错误应用。
- **生产部署**：运行 `./start.prod.sh`（单 API 进程、单任务 worker、无 reload、前端生产构建预览）；`WORKERS` 仅允许为 1。启动前确保 `5173` 和 `8000` 未被其他项目占用。

详细运行、配置与排错说明见 [README.md](README.md)。

## 改动域 affected-check 路由

每次改动完成后，先按 `git diff --name-only` 判断涉及的改动域，再只运行下表中对应的最小检查；所有检查都必须在**最终一次改动之后**重新运行。任一入口失败、依赖或环境缺失时停止继续扩大检查范围，先修复或明确记录阻塞原因，不用其他未列出的命令替代。

| 改动域 | 工作目录与现有入口 | 成功信号 | 停止边界 |
|---|---|---|---|
| 后端 `backend/` | `ruff check backend`；后端跨模块改动运行 `cd backend && pytest -q` | 命令退出码为 0；pytest 输出 `passed` | Ruff 或任一相关 pytest 失败时停止，修复后从最终改动重新运行对应命令 |
| 前端 `frontend/` | `cd frontend && npm run lint`；组件/交互改动追加 `npm run test:run`；构建或路由改动追加 `npm run build` | 各命令退出码为 0；build 完成且无 TypeScript/Vite 错误 | 任一 lint、test 或 build 失败时停止，不以 dev server 或人工点选结果替代 |
| 数据库迁移 `backend/` | `cd backend && .venv/bin/alembic upgrade head`，随后 `.venv/bin/alembic current` | upgrade 退出码为 0；current 能输出当前迁移版本 | 数据库不可连接、迁移失败或版本未更新时停止，不继续运行依赖新 schema 的服务检查 |
| 前端 i18n 字典（仅 `src/i18n/index.tsx` 条目增删，未触碰任何 `t()` 调用点与组件逻辑） | `cd frontend && npm run lint` | 退出码为 0 | 一旦改动涉及 `t()` 调用点或语言切换逻辑，即回归「前端 `frontend/`」行的完整检查；详见 [docs/frontend/i18n.md](docs/frontend/i18n.md) |
| MCP 与编程助手接入（仓库根目录） | 核对客户端 MCP 配置中的启动器绝对路径；MCP API/STDIO 改动追加 `cd backend && pytest -q tests/test_mcp_api.py tests/test_mcp_server.py` | 启动器路径正确；相关 pytest 全部通过 | 配置核对或任一 MCP 测试失败时停止，不以网页成功打开或手工调用替代 |

选择完最小集合后，记录实际命令、工作目录、最终修订和结果；跨域改动必须合并各涉及行，并在最后一次代码或迁移改动后重跑整组检查。
