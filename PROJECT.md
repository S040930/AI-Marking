# AI 作业批改系统

## 项目简介

面向高校作业场景的智能批改平台。教师上传学生作业 PDF 与作业题目 PDF，系统通过 OCR 提取文本，再由 LLM 依据 rubric 生成评分建议；最终由教师在协同评分页与 AI 对话、调整分数并确认最终得分。

## 技术栈

- **前端**：React 19 + Vite + TypeScript + Tailwind CSS v4 + shadcn/ui + React Router v7 + TanStack Query
- **后端**：FastAPI + Uvicorn + SQLAlchemy 2.0 + Alembic + PostgreSQL
- **LLM**：OpenAI Python SDK（兼容 OpenAI Chat Completions 格式服务：豆包、通义千问、DeepSeek、OpenAI、Kimi、Ollama 等）
- **OCR**：PaddleOCR-VL（PDF → 结构化 Markdown）
- **任务队列**：MVP 阶段使用 FastAPI BackgroundTasks，未来可替换为 Celery + Redis

## 目录结构

```
AI-Marking/
├── backend/          # FastAPI 后端
├── frontend/         # React 前端
├── docs/             # 详细设计文档（见下文索引）
├── PROJECT.md        # 本文档：主索引
└── README.md         # 运行与开发说明
```

## 关键设计决策

1. **PDF 仅前端预览**：学生作业与题目 PDF 通过后端安全文件流接口返回，前端使用浏览器原生 iframe 渲染，不引入前端 PDF 解析库，降低 MVP 复杂度。
2. **协同评分流程**：OCR → Agent 评分 → Critic 复核 → 教师 Review（AI 聊天式协作）→ 最终评分确认。
3. **状态分离**：列表/处理中轮询使用轻量 `SubmissionStatusOut`，终态后再拉取完整 `SubmissionDetail`，避免传输 `ocr_text`/`ai_result` 等大字段。
4. **配置入库**：LLM Key、endpoint、PaddleOCR URL/token、自定义 rubric 等全部存入数据库，不依赖 `.env`。
5. **浅色极简设计系统**：以净白为底、Indigo 为主强调色，支持 `prefers-reduced-motion` 与 Skip Link 无障碍访问。

## 子文档索引

| 主题 | 路径 | 说明 |
|---|---|---|
| 协同评分页设计 | [docs/frontend/review-page.md](docs/frontend/review-page.md) | 左右分栏布局、Indigo 浅色聊天面板、评分快照卡片、finalize 确认流程 |

## 快速开始

详见 [README.md](README.md)。
