# AI 作业批改系统

## 项目简介

面向高校作业场景的智能批改平台。教师通过本机编程助手（Codex 等）提交学生报告 PDF 与代码文件，题目 PDF 在网页题目库上传；系统通过 OCR 提取文本，评分由编程助手通过 MCP 完成并保存可审计的建议，最终由教师在网页人工确认最终成绩。后端不再内置 LLM/Agent/Critic 评分链路。

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
  └─ EventSource：/api/submissions/{id}/events SSE 实时推送
           ↓
FastAPI API workers
  ├─ API：questions / submissions / config / admin / health / mcp
  ├─ SQLAlchemy 同步 Session（路由 def，含 await 的路由 async def）
  ├─ Prometheus 指标：/metrics
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
  ├─ 并发上限（默认 4）
  ├─ 首次题目 OCR / 题目新版 OCR / 作业 OCR
  ├─ uploads/ 定期清理
  └─ Prometheus 业务指标埋点（队列深度、任务时长、重试/死信计数、OCR 调用）
           ↑
本机编程助手 MCP（STDIO，任意兼容客户端）
  ├─ run-ai-marking-mcp → FastAPI /api/mcp（loopback），按可选 client 记录评分来源
  ├─ 编程助手内预检报告 PDF + 多语言小题文件，再提交并自动等待 OCR
  ├─ PostgreSQL TTL 评分包与续页句柄读取（MCP API v10）
  ├─ 编程助手评分建议、revision 乐观锁、evidence 校验
  └─ 不提供最终确认、删除、配置或密钥工具
```

### 核心业务边界

- **题目域**：题目 PDF 上传（专门的批量上传页）、题目 OCR、题目复用、替换与删除。
- **批改域**：编程助手提交学生作业、失败原记录重试、作业 OCR、等待 MCP 评分、人工复核和状态流转。
- **审核域**：教师人工核对评分建议、表单式改分、确认最终结果。
- **配置域**：OCR、rubric 与 MCP 自检开关配置。
- **运维域**：Prometheus 指标、死信任务管理（`/api/admin/dead-jobs`）。
- **文件生命周期**：题目和学生 PDF 永久保留并按内容哈希共享；只有记录删除且无其他引用时才清理实体文件。PDF 预览接口同时支持 `GET` 与 `HEAD`，供浏览器预检后再加载。

### 运行模型

- API worker 只在事务内创建业务记录与 `background_jobs` 队列记录。
- 单独的 `python -m app.worker` 进程原子领取任务，并以租约和心跳恢复崩溃任务。
- 任务 worker 内部限制 OCR 并发，默认上限为 4。
- OCR 请求内部仅重试短暂网络错误；最终失败后保留原因，由编程助手重新提交作业。
- 前端通过 SSE 实时推送感知后台任务进度，30s 兜底轮询仅在 SSE 断开时触发；`awaiting_mcp` 保持低频状态刷新直到编程助手保存建议。
- 多个 API worker 不会放大后台执行并发；当前部署仍要求所有进程共享 PostgreSQL 与本地上传目录。

## 架构升级 (P0-P5)

针对前序架构评审发现的 6 个问题已完成改造，详见 [docs/architecture/upgrade-p0-p5.md](docs/architecture/upgrade-p0-p5.md)。

| 优先级 | 问题 | 方案 |
|---|---|---|
| P0 | async 路由 + 同步 Session 反模式 | 纯同步路由改回 `def`；长耗时 LLM 调用随 Agent 链路一并移除 |
| P1 | 前端 2s 轮询 | 基于 PG LISTEN/NOTIFY 的 SSE 推送（`/submissions/{id}/events`），轮询降级为 30s 兜底 |
| P2 | LLM 客户端缓存无失效 | 后端不再持有 LLM 客户端，配置项移除 LLM 相关字段 |
| P3 | 文件存储本地耦合 | 本地部署直接使用 uploads 目录，移除远端 StorageBackend 抽象 |
| P4 | 可观测性缺失 | Prometheus 指标（`/metrics`）+ 死信管理 API（`/api/admin/dead-jobs` 列出/重试/删除） |
| P5 | cleanup 全表加载 | 加 `LIMIT 10000` 保护与注释说明；后续随 P3 StorageBackend 落地统一优化 |

## 后台任务失败语义

worker 依据异常类型区分「业务失败」与「系统失败」，采取不同策略：

- **业务失败**（`BusinessError`）：配置错误、文件不可识别等确定性错误。重试无意义，worker 立即将目标标记为终态（`failed` / `replacement_status=failed`）并删除任务行，**不进入队列退避重试**，也不产生死信。
- **系统失败**（其余 `Exception`）：网络瞬时抖动耗尽、数据库中断、进程崩溃等。worker 保留队列的租约 / 心跳 / 指数退避重试，达到 `TASK_MAX_ATTEMPTS` 上限后转入死信（`dead`），由运维介入。

OCR 在内部对超时 / 限流 / 5xx 进行最多 3 次短暂重试；网络重试耗尽仍属系统失败（可重试），而配置错误 / 4xx 认证 / 返回为空或结构异常则属业务失败（不重试）。

## 关键设计决策

1. **统一 PDF 处理链**：原生 PDF 流式接收并直接持久化，通过后端安全文件流与浏览器原生 iframe 预览。
2. **MCP 评分流程**：OCR → `awaiting_mcp` → 编程助手保存建议 → `ready_for_review` → 教师表单式人工复核 → 最终评分确认。后端不内置 LLM/Agent/Critic。
3. **状态分离**：列表/处理中轮询使用轻量 `SubmissionStatusOut`，终态后再拉取完整 `SubmissionDetail`，避免传输 `ocr_text`/`assessment_suggestion` 等大字段。
4. **配置入库**：PaddleOCR URL/token、结构化 rubric 与 MCP 自检开关全部存入数据库，不依赖 `.env`。
5. **浅色极简设计系统**：以净白为底、Indigo 为主强调色，支持 `prefers-reduced-motion` 与 Skip Link 无障碍访问。
6. **前端语言偏好**：系统界面支持 `zh-CN` 与 `en-US`；首次访问跟随浏览器语言，用户切换后保存到浏览器 `localStorage`，用户业务内容保持原文。
7. **题目与作业解耦**：一个 `Question` 可关联多条 `Submission`，删除单条作业不会删除共享题目。
8. **数据库优先删除**：数据库事务提交成功后再清理文件，避免记录存在但文件提前丢失。
9. **题目新版暂存切换**：新版文档统一生成 PDF 后进入持久化 OCR 队列；成功后原子切换，失败时旧题目继续可用。
10. **最终评分单一入口**：编程助手只保存待确认建议；只有教师显式确认才写入 `reviewed`。`POST /submissions/{id}/finalize` 是写入最终评分的唯一入口。
11. **测试显式建模题目关系**：测试不得自动为 `Submission` 注入默认题目；凡是创建作业记录，都必须显式创建 `Question` 并通过 `question` 或 `question_id` 关联，以避免绕过生产约束。
12. **编程助手单一新建入口**：编程助手评分作业由本机 MCP 预检并提交，在同一任务内等待 OCR；网页端仅保留题目库上传，学生作业一律由编程助手提交。
13. **MCP 服务身份与版本校验**：健康响应同时声明 `service` 和 API 版本；启动脚本拒绝复用 8000 端口，诊断工具区分错误服务、旧进程、token 和数据库问题。
14. **本地 API 代理固定 IPv4**：Vite 将 `/api` 转发到 `127.0.0.1:8000`，避免 `localhost` 被优先解析为 IPv6 `::1` 而后端只监听 IPv4 时产生“网络连接异常”。外部评分记录通过 `mcp_metadata.client` 显示客户端配置中的名称，不保存未经验证的模型申报值。
15. **报告—代码联动证据**：编程助手可提交一份报告 PDF 和按小题映射的 Python/Notebook、R、Java、C/C++ 代码组；后端只保存源码与 SHA-256。代码由当前编程助手任务在临时目录中尝试运行，结果留在对话中；含代码作业评分前需取得教师人工一致性确认，教师仍是最终确认者。
16. **统一 rubric 解析**：题目可信 OCR 提取快照（客户端提交、服务端确定性校验）优先，其次配置项目中的结构化 rubric，最后是内置默认；网页与编程助手 MCP 使用同一 Resolver、snapshot ID 和逐项校验。
17. **双遍精评**：ai-marking-grader 先按 rubric 建立证据账本并逐项评分，再独立反向复核证据、部分得分、宽严一致性和重复扣分；反馈提供可执行改进动作，保存前仍只生成教师待确认建议。
18. **MCP v10 协议收敛**：编程助手正常评分使用预检、提交、打开、待办列表、rubric 提取、人工确认与保存工具；不接受视觉比较输入、自由 rubric 文本、运行日志或代码产物。预检计划仅保留文件元数据并主动清理过期项，服务端评分句柄持久化于 PostgreSQL 并绑定文件/上下文，评分包不包含后端执行信息，评分政策只由评分包提供。题目 rubric 不再由后端 LLM 生成：`open_ai_marking_assignment` 返回 `needs_rubric` 时由客户端从题目 OCR 提取，服务端以「引用必须是 OCR 原文子串且包含满分、各项满分之和等于总分、评分项不重复」确定性校验后写入权威快照。
19. **本地文件按内容寻址**：PDF 使用 SHA-256 作为共享实体路径，原始文件名只作为记录元数据保留；清理和题目替换删除前会查询题目、作业及替换引用，永不删除仍被引用的文件。开发依赖由 `scripts/clean-local` 和 `scripts/bootstrap` 按需清理、恢复。

## 子文档索引

| 主题 | 路径 | 说明 |
|---|---|---|
| 架构升级 P0-P5 | [docs/architecture/upgrade-p0-p5.md](docs/architecture/upgrade-p0-p5.md) | async 路由改造、SSE 推送、Prometheus 指标、cleanup 优化 |
| 协同评分页设计 | [docs/frontend/review-page.md](docs/frontend/review-page.md) | 左右分栏布局、等待 MCP 面板、表单式人工改分、finalize 确认流程 |
| 编程助手 MCP 评分 | [docs/architecture/mcp-grading.md](docs/architecture/mcp-grading.md) | STDIO 连接、状态机、PDF+代码证据、工具契约、安全边界与排错 |
| 编程助手评分 skill | [.agents/skills/ai-marking-grader/SKILL.md](.agents/skills/ai-marking-grader/SKILL.md) | 预检、提交、打开评分包、rubric 提取、双遍精评与教师复核；评分政策由服务端评分包提供 |
| 数据模型 | [docs/data/schema.md](docs/data/schema.md) | 数据表职责、关系、状态和文件生命周期 |

## 快速开始

- **编程助手首次接入**：在客户端 MCP 配置中填写 `scripts/run-ai-marking-mcp` 的绝对路径和可选客户端名称参数；代码只由当前编程助手任务在临时目录中尝试运行，后端不执行学生代码。
- **本地开发**：配置好数据库后运行 `./start.sh`（自动安装依赖、迁移数据库、校验后端身份并启动前后端与 worker，热重载）。前端严格使用 `5173`，后端严格使用 `8000`；端口被占用时直接报错，避免误连到错误应用。
- **生产部署**：运行 `./start.prod.sh`（多 worker、无 reload、前端生产构建预览），可通过 `WORKERS` 环境变量覆盖 worker 数。启动前确保 `5173` 和 `8000` 未被其他项目占用。

详细运行、配置与排错说明见 [README.md](README.md)。

## 改动域 affected-check 路由

每次改动完成后，先按 `git diff --name-only` 判断涉及的改动域，再只运行下表中对应的最小检查；所有检查都必须在**最终一次改动之后**重新运行。任一入口失败、依赖或环境缺失时停止继续扩大检查范围，先修复或明确记录阻塞原因，不用其他未列出的命令替代。

| 改动域 | 工作目录与现有入口 | 成功信号 | 停止边界 |
|---|---|---|---|
| 后端 `backend/` | `cd backend && ruff check app tests`；后端跨模块改动运行 `pytest -q` | 命令退出码为 0；pytest 输出 `passed` | Ruff 或任一相关 pytest 失败时停止，修复后从最终改动重新运行对应命令 |
| 前端 `frontend/` | `cd frontend && npm run lint`；组件/交互改动追加 `npm run test:run`；构建或路由改动追加 `npm run build` | 各命令退出码为 0；build 完成且无 TypeScript/Vite 错误 | 任一 lint、test 或 build 失败时停止，不以 dev server 或人工点选结果替代 |
| 数据库迁移 `backend/` | `cd backend && .venv/bin/alembic upgrade head`，随后 `.venv/bin/alembic current` | upgrade 退出码为 0；current 能输出当前迁移版本 | 数据库不可连接、迁移失败或版本未更新时停止，不继续运行依赖新 schema 的服务检查 |
| MCP 与编程助手接入（仓库根目录） | 核对客户端 MCP 配置中的启动器绝对路径；MCP API/STDIO 改动追加 `cd backend && pytest -q tests/test_mcp_api.py tests/test_mcp_server.py` | 启动器路径正确；相关 pytest 全部通过 | 配置核对或任一 MCP 测试失败时停止，不以网页成功打开或手工调用替代 |

选择完最小集合后，记录实际命令、工作目录、最终修订和结果；跨域改动必须合并各涉及行，并在最后一次代码或迁移改动后重跑整组检查。
