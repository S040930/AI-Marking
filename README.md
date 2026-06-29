# AI 作业批改系统 (SURF-2026-0031)

基于 AI Agent 的作业批改辅助系统,集成 PaddleOCR-VL 文档解析与大语言模型(LLM)实现自动化作业批改与反馈生成。

## 技术栈

| 类别 | 技术 |
| --- | --- |
| 前端 | React + TypeScript + Vite + Ant Design |
| 后端 | Python + FastAPI + SQLAlchemy 2.0 + Alembic |
| 数据库 | PostgreSQL |
| OCR | PaddleOCR-VL(文档解析,输出结构化 Markdown) |
| LLM | OpenAI 兼容协议(支持豆包/通义/DeepSeek/OpenAI/Kimi 等,默认豆包) |

## 环境要求

- Python >= 3.10
- Node.js >= 18
- PostgreSQL >= 14

## 快速启动

### 1. 数据库

创建 PostgreSQL 数据库:

```sql
CREATE DATABASE ai_marking;
```

### 2. 后端

```bash
cd backend

# 安装依赖(含开发工具)
pip install -e ".[dev]"

# 配置环境变量
cp .env.example .env
# 编辑 .env 填写数据库连接等

# 执行数据库迁移
alembic upgrade head

# 启动开发服务器
uvicorn app.main:app --reload
```

默认监听 `http://localhost:8000`,Swagger 文档位于 `http://localhost:8000/docs`。

### 3. 前端

```bash
cd frontend
npm install
npm run dev
```

默认监听 `http://localhost:5173`,开发代理将 `/api` 转发到后端。

## 环境变量

后端通过 `backend/.env` 文件配置(参考 `.env.example`):

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 连接字符串 | `postgresql+psycopg2://postgres:postgres@localhost:5432/ai_marking` |
| `CORS_ORIGINS` | 允许的前端跨域来源 | `["http://localhost:5173"]` |
| `UPLOAD_DIR` | PDF 上传存储目录 | `./uploads` |

## API 配置说明

LLM 与 OCR 的 API Key、Endpoint、评分标准等**业务配置**通过前端设置页面(`/settings`)管理,存储在数据库中,无需修改 `.env` 或重启服务。

配置项包括:

- **LLM**:API Key、Base URL、Model / Endpoint ID(OpenAI 兼容协议)
- **OCR**:PaddleOCR-VL API URL、Access Token
- **评分标准**:自定义 rubric(留空使用内置默认)

## 开发命令

### 后端

```bash
cd backend

# 代码检查
ruff check app/ tests/ alembic/

# 代码格式化
black app/ tests/ alembic/

# 运行测试
pytest -v
```

### 前端

```bash
cd frontend

# 代码检查
npm run lint

# 代码格式化
npm run format

# 构建生产包
npm run build
```
