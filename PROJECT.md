# AI 作业批改系统

## 项目简介

面向高校作业场景的智能批改平台。教师上传 PDF 或 DOCX 格式的学生作业与题目，DOCX 先转换为 PDF，系统再通过 OCR 提取文本并由 LLM 依据 rubric 生成评分建议；最终由教师在协同评分页与 AI 对话、调整分数并确认最终得分。

## 技术栈

- **前端**：React 19 + Vite + TypeScript + Tailwind CSS v4 + shadcn/ui + React Router v7 + TanStack Query
- **后端**：FastAPI + Uvicorn + SQLAlchemy 2.0 同步 Session + Alembic + PostgreSQL
- **LLM**：OpenAI Python SDK（兼容 OpenAI Chat Completions 格式服务：豆包、通义千问、DeepSeek、OpenAI、Kimi、Ollama 等）
- **文档转换**：LibreOffice 26.2.4（DOCX → PDF）
- **OCR**：PaddleOCR-VL（PDF → 结构化 Markdown）
- **任务队列**：PostgreSQL 持久化队列 + 独立 worker

## 目录结构

```
AI-Marking/
├── backend/          # FastAPI 后端
├── frontend/         # React 前端
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
      └─ Axios：/api 请求
           ↓
FastAPI API workers
  ├─ API：questions / submissions / config / health
  ├─ SQLAlchemy 同步 Session
  ├─ DOCX 校验与 LibreOffice 隔离转换
  └─ 本地 uploads/ 转换后 PDF 文件
           ↓
PostgreSQL 持久化队列
  ├─ questions             ├─ PaddleOCR-VL
  ├─ submissions           └─ OpenAI 兼容 LLM API
  ├─ conversations
  ├─ background_jobs
  └─ system_config
           ↑
独立任务 worker
  ├─ 租约、心跳、崩溃恢复与限次重试
  ├─ 并发上限（默认 4）
  ├─ 首次题目 OCR / 题目新版 OCR / 作业批改
  └─ uploads/ 定期清理
```

### 核心业务边界

- **题目域**：PDF/DOCX 上传、题目 OCR、题目复用、替换与删除。
- **批改域**：学生作业上传、失败原记录重试、作业 OCR、Agent 评分、复核和状态流转。
- **审核域**：教师与 AI 对话、修改评分、确认最终结果。
- **配置域**：OCR、LLM、rubric 与操作人配置。
- **文件生命周期**：DOCX 仅在隔离临时目录中用于转换；题目 PDF 受数据库引用保护，学生 PDF 按保留期清理。

### 运行模型

- API worker 只在事务内创建业务记录与 `background_jobs` 队列记录。
- 单独的 `python -m app.worker` 进程原子领取任务，并以租约和心跳恢复崩溃任务。
- 任务 worker 内部限制 OCR/LLM 并发，默认上限为 4。
- DOCX 上传请求最多等待 120 秒完成转换；转换失败不会创建业务记录或留下临时文件。
- OCR 请求内部仅重试短暂网络错误；最终失败后保留原因，由教师重新上传 PDF 或 DOCX。
- 前端通过轮询题目和作业状态感知后台任务进度。
- 多个 API worker 不会放大后台执行并发；当前部署仍要求所有进程共享 PostgreSQL 与本地上传目录。

## 后台任务失败语义

worker 依据异常类型区分「业务失败」与「系统失败」，采取不同策略：

- **业务失败**（`BusinessError` / `AgentError`）：配置错误、文件不可识别、LLM 明确拒绝等确定性错误。重试无意义，worker 立即将目标标记为终态（`failed` / `replacement_status=failed`）并删除任务行，**不进入队列退避重试**，也不产生死信。
- **系统失败**（其余 `Exception`）：网络瞬时抖动耗尽、数据库中断、进程崩溃等。worker 保留队列的租约 / 心跳 / 指数退避重试，达到 `TASK_MAX_ATTEMPTS` 上限后转入死信（`dead`），由运维介入。

OCR 在内部对超时 / 限流 / 5xx 进行最多 3 次短暂重试；网络重试耗尽仍属系统失败（可重试），而配置错误 / 4xx 认证 / 返回为空或结构异常则属业务失败（不重试）。

## 关键设计决策

1. **统一 PDF 处理链**：原生 PDF 直接保存，DOCX 通过 LibreOffice 26.2.4 转换；仅持久化 PDF，并通过后端安全文件流与浏览器原生 iframe 预览。
2. **协同评分流程**：OCR → Agent 评分 → Critic 复核 → 教师 Review（AI 聊天式协作）→ 最终评分确认。
3. **状态分离**：列表/处理中轮询使用轻量 `SubmissionStatusOut`，终态后再拉取完整 `SubmissionDetail`，避免传输 `ocr_text`/`ai_result` 等大字段。
4. **配置入库**：LLM Key、endpoint、PaddleOCR URL/token、自定义 rubric 等全部存入数据库，不依赖 `.env`。
5. **浅色极简设计系统**：以净白为底、Indigo 为主强调色，支持 `prefers-reduced-motion` 与 Skip Link 无障碍访问。
6. **题目与作业解耦**：一个 `Question` 可关联多条 `Submission`，删除单条作业不会删除共享题目。
7. **数据库优先删除**：数据库事务提交成功后再清理文件，避免记录存在但文件提前丢失。
8. **题目新版暂存切换**：新版文档统一生成 PDF 后进入持久化 OCR 队列；成功后原子切换，失败时旧题目继续可用。
9. **最终评分单一入口**：AI 对话只生成待确认建议，只有教师显式确认才写入 `reviewed`；`ChatResponse.finalize_payload` 是前端提交最终评分的唯一权威载荷，`action=finalize` 必须满足该字段非空。
10. **测试显式建模题目关系**：测试不得自动为 `Submission` 注入默认题目；凡是创建作业记录，都必须显式创建 `Question` 并通过 `question` 或 `question_id` 关联，以避免绕过生产约束。

## 子文档索引

| 主题 | 路径 | 说明 |
|---|---|---|
| 协同评分页设计 | [docs/frontend/review-page.md](docs/frontend/review-page.md) | 左右分栏布局、Indigo 浅色聊天面板、评分快照卡片、finalize 确认流程 |
| 数据模型 | [docs/data/schema.md](docs/data/schema.md) | 数据表职责、关系、状态和文件生命周期 |

## 快速开始

- **本地开发**：配置好 `backend/.env` 后运行 `./start.sh`（自动安装依赖、迁移数据库并启动前后端，热重载）。
- **生产部署**：运行 `./start.prod.sh`（多 worker、无 reload、前端生产构建预览），可通过 `WORKERS` 环境变量覆盖 worker 数。

详细运行、配置与排错说明见 [README.md](README.md)。
