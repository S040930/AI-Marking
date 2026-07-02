# AI 作业批改系统 (SURF-2026-0031)

基于 AI Agent 的作业批改辅助系统,集成 PaddleOCR-VL 文档解析与大语言模型(LLM)实现自动化作业批改与反馈生成。

## 技术栈

| 类别 | 技术 |
| --- | --- |
| 前端 | React + TypeScript + Vite + Tailwind CSS + shadcn/ui |
| 后端 | Python + FastAPI + SQLAlchemy 2.0 + Alembic |
| 数据库 | PostgreSQL |
| OCR | PaddleOCR-VL(文档解析,输出结构化 Markdown) |
| LLM | OpenAI 兼容协议(支持豆包/通义/DeepSeek/OpenAI/Kimi 等,默认豆包) |
| Agent | LangGraph(受约束评分、独立复核、限次修正与人工审核) |

## 环境要求

- Python >= 3.10
- Node.js >= 18
- PostgreSQL >= 14

## 快速启动

### 一键启动（推荐）

首次配置好 `backend/.env` 后，在项目根目录运行：

```bash
./start.sh
```

脚本会自动安装缺失依赖、执行数据库迁移，并同时启动前端和后端。按
`Ctrl+C` 可一起关闭两个服务。

### 1. 配置数据库

PostgreSQL 是数据库服务；一个服务中可以包含多个数据库。安装并初始化后看到
的 `postgres`、`template1` 和与 macOS 用户同名的数据库属于默认数据库，
项目仍需单独创建 `ai_marking`。

先确认 PostgreSQL 的实际端口。默认端口为 `5432`；如果使用 Postgres.app，
以界面中服务器卡片显示的端口为准，例如 `5555`：

```bash
pg_isready -h localhost -p 5555
```

显示 `accepting connections` 后创建项目数据库：

```bash
createdb -h localhost -p 5555 -U mac ai_marking
```

其中 `5555` 替换为实际端口，`mac` 替换为实际 PostgreSQL 用户名。用户名通常
与 macOS 用户名一致，可通过 `whoami` 查看。

复制并编辑后端环境配置：

```bash
cp backend/.env.example backend/.env
```

无密码的本地 Postgres.app 示例：

```env
DATABASE_URL=postgresql+psycopg2://mac@localhost:5555/ai_marking
CORS_ORIGINS=["http://localhost:5173"]
UPLOAD_DIR=./uploads
```

使用默认端口、用户名和密码的示例：

```env
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/ai_marking
```

如密码含 `@`、`:`、`/` 等特殊字符，需要先进行 URL 编码。

#### 使用 pgAdmin 4 查看数据库

在 `Servers → Register → Server` 中填写：

```text
Name: AI-Marking
Host name/address: localhost
Port: 5555
Maintenance database: postgres
Username: mac
Password: 按本地配置填写；无密码时留空
```

保存后展开 `Servers → AI-Marking → Databases`，即可看到 `ai_marking`。如果
没有立即出现，右键 `Databases` 选择 `Refresh`。

> 如果 Postgres.app 显示 `Port in use`，表示该端口已被另一个实例占用。
> 使用 `pg_isready` 确认现有实例可连接即可，不要同时启动两个使用相同端口
> 的 PostgreSQL 服务。

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

## 常见启动问题

- **数据库迁移失败**：检查 `DATABASE_URL` 中的主机、端口、用户名、密码和
  数据库名是否与实际配置一致。
- **Connection refused**：通常是 PostgreSQL 未启动或端口写错，例如误将
  `5555` 写成 `555`。
- **Port in use**：使用 `lsof -nP -iTCP:<端口> -sTCP:LISTEN` 查看占用端口
  的进程；已有 PostgreSQL 正常运行时可直接使用该实例。
- **database "ai_marking" does not exist**：先执行 `createdb` 命令创建项目
  数据库。

## API 配置说明

LLM 与 OCR 的 API Key、Endpoint、评分标准等**业务配置**通过前端设置页面(`/settings`)管理,存储在数据库中,无需修改 `.env` 或重启服务。

配置项包括:

- **LLM**:API Key、Base URL、Model / Endpoint ID(OpenAI 兼容协议)
- **OCR**:PaddleOCR-VL API URL、Access Token
- **评分标准**:自定义 rubric(留空使用内置默认)

## Agent 批改流程

后端使用受约束的 LangGraph 状态图执行批改:

```text
OCR → 评分 Agent → 结构与分数校验 → Critic 复核
                                      ├─ 通过 → 完成
                                      ├─ 修正 → 重新评分(最多一次)
                                      └─ 不确定 → 人工审核
```

结果页展示不含隐藏推理的执行摘要、复核置信度与风险原因。进入人工审核的
记录可由教师编辑分数和反馈后确认，系统同时保留原始 AI 评分用于审计。

## 前端设计

前端采用浅色极简风格,以靛蓝(Indigo)为主强调色,配合克制的高级灰与清晰的
信息层级,营造专业、干净的批改工具体验。

主要设计原则:

- **浅色优先**:默认使用净白/浅灰背景,不启用 Dark 模式。
- **微交互动效**:卡片 hover 轻抬升、按钮按压缩放、页面元素淡入,所有动效
  均支持 `prefers-reduced-motion` 降级。
- **响应式布局**:基于 Tailwind 断点适配桌面与较小屏幕,重点页面(如协同
  评分)在大屏左右分栏、小屏上下堆叠。
- **无障碍**:包含 Skip Link、键盘焦点环、减少动态效果适配。

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

# 运行测试
npm run test:run
```
