# AI 作业批改系统

基于 OCR 与**后端内嵌批改 agent** 的作业批改辅助系统。系统集成 PaddleOCR-VL
文档解析；评分由后端进程内的自研 agent（tool-calling 循环，官方 OpenAI API
通道）完成并保存可审计建议，教师最终在网页确认。评分不再依赖外部编程助手、
ACP 或 MCP。

## 主要功能

- **PDF 与 ZIP 上传**：题目 PDF 通过专门的题目上传页批量上传（拖拽/选择，单文件 ≤50MB）并直接进入 OCR 链路；学生作业在作业上传页提交纯 PDF，或一份 ZIP 作业包（≤150MB，内含恰好一份报告 PDF、文件名以小题号结尾的代码文件（如 `task3.py`）与其余文件作为数据集）。选中 ZIP 后前端只读解析中央目录即时预览分类结果，上传后由后端解包权威校验并自动分类，数据集随批改物化到隔离工作区。
- **独立题目库**：题目只需上传并 OCR 一次，后续可直接复用于多份学生作业。
- **AI 批改 agent**：进入作业详情页（`awaiting_grading`）后选择模型通道与思考强度并点「开始批改」；后端批改 worker 驱动内嵌 agent 读取评分包、完成证据化评分并保存建议；含代码作业在评分前弹出教师检查点（可在本地运行学生代码后核对，或直接确认）。运行进度经 SSE 实时转录，运行中可热切换模型/思考强度（下一回合生效）。
- **模型通道配置**：设置页提供官方 OpenAI API 通道（API key 只写不读、连接测试、默认模型与思考强度）与 ChatGPT 订阅通道占位（未开放）；可设置默认批改通道。
- **人工复核与确认**：教师可查看原文证据、表单式调整单项分数与反馈并确认最终评分。
- **历史记录管理**：按处理状态查看、选择和安全删除批改记录。
- **题目安全管理**：支持搜索、预览、重命名、OCR 重试、上传新版及级联删除。
- **中英文界面**：网页顶栏可在简体中文与英文间切换；语言偏好会保存在浏览器中，并在首次访问时跟随浏览器语言。

## 技术栈

| 类别 | 技术 |
| --- | --- |
| 前端 | React + TypeScript + Vite + Tailwind CSS + shadcn/ui |
| 后端 | Python + FastAPI + SQLAlchemy 2.0 + Alembic |
| 数据库 | PostgreSQL |
| OCR | PaddleOCR-VL(文档解析,输出结构化 Markdown) |
| 评分 | 后端内嵌 agent（进程内 tool-calling；httpx → OpenAI Responses API） |
| 依赖管理 | uv（`backend/uv.lock` 为锁文件；脚本用 `uv sync`） |

## 环境要求

- Python 3.12.13（见 `.python-version`）
- Node.js 26.4.0（见 `.nvmrc`）
- PostgreSQL >= 14

## 快速启动

### 一键启动（推荐）

首次配置好 `backend/.env` 后，在项目根目录运行：

```bash
./start.sh
```

脚本会自动安装缺失依赖（uv / npm）、执行数据库迁移、确认 8000 端口没有旧服务，并同时
启动前端、后端、任务 worker 与批改 worker。按 `Ctrl+C` 可一起关闭四个进程。

### 本机磁盘清理与恢复

开发依赖和构建产物不属于业务数据。查看可回收空间（默认不会删除）：

```bash
./scripts/clean-local
```

确认后清理，再按需重建开发环境：

```bash
./scripts/clean-local --apply
./scripts/bootstrap
```

清理脚本只处理固定的 `.venv`、`node_modules`、构建产物和缓存白名单，永不触碰
`backend/uploads`、数据库、`.env` 或 `.git`。完整恢复开发环境约需 400MB；未安装
依赖时项目目录通常约 15–25MB。

### 生产启动

构建前端生产包并以本机优化模式运行（无热重载）：

```bash
./start.prod.sh
```

脚本固定启动一个 API 进程、一个任务 worker 与一个批改 worker；`WORKERS` 只能为 `1`，更大的值会
直接拒绝启动，避免单机数据库连接池和本地配置语义随进程数放大。

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

# 安装依赖（uv 按 uv.lock 精确安装到 backend/.venv，含开发工具）
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env 填写数据库连接等

# 执行数据库迁移
.venv/bin/alembic upgrade head

# 启动开发服务器
.venv/bin/uvicorn app.main:app --reload
```

默认监听 `http://localhost:8000`。出于安全考虑已禁用 Swagger 文档
(`/docs`、`/openapi.json` 关闭,避免绕过访问令牌暴露 API 全貌)。

### 3. 前端

```bash
cd frontend
npm ci
npm run dev
```

网页默认根据浏览器语言选择中文或英文，也可以在页面顶栏使用 `English` / `中文` 按钮切换。切换结果保存在当前浏览器的 `localStorage` 中；题目名称、OCR 原文和评分反馈等用户数据保持原文。

默认监听 `http://localhost:5173`,开发代理将 `/api` 转发到后端。项目会严格占用 `5173`；如果启动时报端口被占用，请先停止占用该端口的其他项目，不要打开它自动切换后的其他端口。

如果页面出现 `加载配置失败 / 404`，先确认访问的是本项目进程：

```bash
lsof -nP -iTCP:5173 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
curl http://localhost:8000/api/config
```

`/api/config` 应返回 JSON 配置；如果响应头或 OpenAPI 显示的是其他项目，请回到该项目终端按 `Ctrl+C` 停止它，再从本目录运行 `./start.sh`。

## AI 批改 agent

批改由后端内嵌 agent（`backend/app/agent/**`）在独立 worker 进程内完成：

- 教师在评审页（`/review/{id}`，作业状态 `awaiting_grading`）选择模型通道与
  思考强度后点「开始批改」；
- 后端创建 `GradingRun` 并由批改 worker 领取租约，物化作业工作区、组装评分包，
  引擎在 tool-calling 循环内完成证据化评分并调用 `save_assessment` 保存建议；
- 含代码作业在评分前触发教师检查点（run 驻留 `waiting_for_teacher`，可跨夜，
  答复后续跑）；运行中可热切换模型/思考强度（下一回合生效）；
- 运行进度经 SSE（`GET /api/agent/runs/{id}/stream`）实时转录；取消使用
  `POST /api/agent/runs/{id}/cancel`。

批改 worker 独立运行：

```bash
cd backend
.venv/bin/python -m app.grading_worker
```

模型通道在「系统设置」配置：官方 OpenAI API 通道（API key 只写不读，响应仅
回显后四位；可做连接测试与设置默认模型/思考强度）；ChatGPT 订阅通道为未开放
占位。查询接口为 `GET /api/agent/providers`；运行中配置切换使用
`PATCH /api/agent/runs/{id}/configuration`。

完整状态机、工具契约、检查点与恢复语义见
[docs/architecture/agent-grading.md](docs/architecture/agent-grading.md)。

## 环境变量

后端通过 `backend/.env` 文件配置(参考 `.env.example`):

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 连接字符串 | `postgresql+psycopg2://postgres:postgres@localhost:5432/ai_marking` |
| `CORS_ORIGINS` | 允许的前端跨域来源 | `["http://localhost:5173"]` |
| `UPLOAD_DIR` | 上传 PDF 的存储目录 | `./uploads` |
| `UPLOAD_RETENTION_DAYS` | 无数据库引用孤儿文件的清理宽限期（引用文件永不定期删除） | `7` |
| `CLEANUP_INTERVAL_SECONDS` | 后台清理任务扫描间隔(秒) | `3600` |
| `TASK_CONCURRENCY` | 独立任务 worker 最大并发数 | `2` |
| `TASK_LEASE_SECONDS` | 任务租约秒数 | `90` |
| `TASK_MAX_ATTEMPTS` | 非预期异常最大执行次数 | `3` |
| `TASK_POLL_INTERVAL_SECONDS` | 空队列轮询起始间隔（指数退避至 5 秒） | `1` |
| `GRADING_MAX_CONTEXT_CHARS` | 批改 agent 评分上下文总字符硬上限；超过后需拆分作业或缩减提交内容 | `200000` |
| `ACCESS_TOKEN` | 全站访问令牌；留空关闭鉴权，设置后网页与管理 API 均需认证 | 空 |
| `MAX_SSE_CLIENTS` | PostgreSQL SSE LISTEN 连接上限 | `16` |
## 代码执行

后端不编译、执行或上传学生代码产物，也不承担学生代码的 CPU、内存、进程或磁盘资源。
含代码作业在批改 run 内触发**教师检查点**：教师把同题源码复制到独立临时目录自行尝试
运行（不修改原文件、不申请提权、不开放网络），核对运行表现与报告描述后确认「一致」，
或选「不一致」并填写说明；运行输出只存在于教师本地，不作为服务端证据。后端仅持久化
源码、SHA-256、评分建议和人工确认。

## 一次完整批改

1. 在网页题目库上传题目 PDF，等待 OCR 变为「可使用」（题目只需识别一次）。
2. 在作业上传页选择题目并提交学生作业：纯报告 PDF，或一份 ZIP 作业包
   （报告 PDF + 文件名以小题号结尾的代码 + 数据集；报告 PDF ≤ 50 MB，
   代码 ≤ 20 个文件、总计 100 MB、单文件 20 MB）。提交后自动跳转评审页。
3. 作业 OCR 完成后状态变为 `awaiting_grading`，评审页显示开始面板：选择
   模型通道（官方 OpenAI API）与思考强度，点「开始批改」。
4. 后端批改 worker 驱动内嵌 agent 完成评分：以不透明续页令牌分页读取评分包
   →（题目无可信 rubric 时）先从题目 OCR 提取评分标准并经服务端确定性校验
   保存 →（含代码作业）弹出教师检查点，教师在本地运行学生代码核对后确认
   「一致」，或选「不一致」并填写说明 → 逐项证据化评分（零分且无内容可用
   `not_found` 免证据）→ `save_assessment` 保存建议（服务端生成 request_id，
   同 payload 幂等）。
5. 建议保存后作业进入 `ready_for_review`，教师在评审页核对证据、表单式调整
   分数并确认最终成绩；页面同时提供「使用原文件重试」等失败恢复入口。

批改全程不需要任何外部编程助手；如批改失败，可在评审页重新配置并发起。

完整状态机、工具契约、检查点跨夜与恢复语义见
[docs/architecture/agent-grading.md](docs/architecture/agent-grading.md)。

## 持久化任务 worker

首次题目 OCR、题目新版 OCR 和作业 OCR 不在 FastAPI 请求进程中执行。上传接口在同一数据库事务
内创建业务记录和 `background_jobs` 任务，独立 worker 再从 PostgreSQL 原子
领取任务：

```bash
cd backend
.venv/bin/python -m app.worker          # OCR / 清理 worker
.venv/bin/python -m app.grading_worker  # 批改 worker（并发 1）
```

`start.sh` 与 `start.prod.sh` 已自动启动两个 worker。生产脚本固定一个 API 进程、
一个任务 worker 与一个批改 worker，通过 `TASK_CONCURRENCY` 控制 OCR 并发（默认 2）。
进程异常退出后，运行中任务会在租约到期后被重新领取；执行容量满时新任务
保持 `pending` 排队，不再返回队列繁忙 503。

上传支持单个最大 50 MB 的 PDF；ZIP 作业包最多 20 个代码文件、总计 100 MB，单文件 20 MB。后端不执行学生代码，学生代码由教师在批改检查点环节自行在本地运行核对。OCR 会在单次任务内对超时、限流和服务端错误进行最多 3 次短暂重试。仍失败
时页面会保留错误原因；题目可重新上传 PDF，学生作业可在原记录上使用已保存的 PDF
重试。批改 agent 只保存待确认评分，教师点击确认后才写入最终结果。

## 使用流程

### 首次批改某个题目

1. 进入「题目库」上传 PDF 题目，等待 OCR 状态变为「可使用」。
2. 先在「系统设置」配置模型通道（官方 OpenAI API key），并确认 PaddleOCR 配置可用。
3. 在作业上传页选择该题目并提交学生作业（报告 PDF 或 ZIP 作业包）。
4. 作业 OCR 完成后进入评审页：选择模型与思考强度，点「开始批改」；含代码
   作业按提示完成教师检查点。
5. 评分建议保存后，在评审页查看评分详情并确认最终评分。

### 继续批改同一题目

重复第 3 步提交同一题目的新学生作业即可，无需重复上传或识别题目。

### 失败后重新批改

失败记录可在评审页使用已保存的 PDF 重新入队（「使用原文件重试」）。
重试沿用原 submission ID，并清空旧 OCR 与评分建议。若原 PDF 已
被定期清理，需要重新提交。批改 run 失败后可回到开始面板重新配置发起。

### 批改其他题目

在题目上传页（题目库 →「上传题目」）批量上传 PDF 题目；OCR 完成后即可为
该题目提交学生作业。

> 上传新版会先进入后台 OCR 队列，处理期间题目暂时冻结。新版成功后才清理
> 关联的终态批改记录并原子切换；新版失败时旧题目继续可用。若仍有批改任务
> 正在处理，系统会拒绝操作。危险操作需要输入完整题目名称确认。

## 常见启动问题

- **数据库迁移失败**：检查 `DATABASE_URL` 中的主机、端口、用户名、密码和
  数据库名是否与实际配置一致。
- **Connection refused**：通常是 PostgreSQL 未启动或端口写错，例如误将
  `5555` 写成 `555`。
- **Port in use**：使用 `lsof -nP -iTCP:<端口> -sTCP:LISTEN` 查看占用端口
  的进程；`start.sh` 会拒绝复用已占用的 8000 端口，避免前端或其他客户端
  误连其他服务或旧 AI-Marking 进程。已有 PostgreSQL 正常运行时可直接使用
  该数据库实例。
- **批改一直排队**：确认批改 worker 已启动（`python -m app.grading_worker`），
  且作业状态为 `awaiting_grading`、系统设置中官方通道已配置 API key。
- **database "ai_marking" does not exist**：先执行 `createdb` 命令创建项目
  数据库。
- **`DuplicateObjectError: question_status already exists`**：更新到最新代码后
  重新运行 `alembic upgrade head`。迁移使用事务执行，失败时不会留下半迁移
  数据。

## 数据库迁移与数据清理

每次拉取包含数据库结构变更的新代码后执行：

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/alembic current
```

当前迁移链已包含内嵌批改 agent 改造（`grading_runs` 重构与契约改名）。旧迁移
文件必须保留，用于新环境建库、升级和回滚。

如需清空题目与作业数据，同时保留系统配置、表结构及迁移版本：

```sql
TRUNCATE TABLE submissions, questions
RESTART IDENTITY CASCADE;
```

该操作不可恢复，且不会删除 `backend/uploads` 中残留的 PDF 文件。

## API 配置说明

OCR、评分标准与模型通道等**业务配置**通过前端设置页面(`/settings`)管理,存储在数据库中,无需修改 `.env` 或重启服务。

配置项包括:

- **模型通道**:官方 OpenAI API 的 API key（只写不读，仅回显后四位）、API 地址、默认模型与思考强度；默认批改通道
- **OCR**:PaddleOCR-VL API URL、Access Token
- **评分标准**:题目可信提取 rubric 优先，其次使用配置的结构化 rubric（条目、满分、说明），最后回退内置默认

## 批改流程

题目入库时先独立完成一次 OCR。每份学生作业使用缓存的题目文本，避免
重复调用题目 OCR。作业 OCR 完成后进入 `awaiting_grading`，由教师发起
批改 run，后端 agent 在独立 worker 内完成评分：

```text
题目库 OCR（仅首次）
        ↓
学生作业 OCR → awaiting_grading → 教师发起批改 run（内嵌 agent 证据化评分）→ ready_for_review
                                                                                  ↓
                                                   （可选）独立复核结论展示（v1 只读）
                                                                                  ↓
                                                            教师在网页人工复核并确认最终成绩
```

批改运行由独立 `grading_worker` 进程内的 agent 引擎完成，API 进程只承载
HTTP/SSE。agent 保存的是待确认建议；教师确认后写入最终结果，
系统保留建议与最终成绩用于审计。

## 导出 Excel 成绩分析

题目库中每张已有批改记录的题目卡片都提供“导出分析”按钮。点击后输入本次
及格线（默认 `60%`），系统会读取该题目下所有已经由教师确认的 `reviewed`
最终成绩，并立即下载一个 `.xlsx` 工作簿。界面为中文时生成中文工作簿，切换为
English 后生成英文工作簿。

工作簿包含三个工作表：

- **结果分析**：已审阅人数、平均/中位/最高/最低得分率、及格率、固定五档分布、
  评分项平均得分率、两张图表及基于这些统计值生成的客观说明。
- **成绩总表**：学生文件名、最终得分、满分、公式计算的得分率和是否达标、审核
  教师、审核时间、总体反馈及 rubric 快照 ID。
- **评分项明细**：每名学生的逐项得分、逐项满分、公式计算的得分率、评语和证据。

及格线只影响“是否达标”和及格率；分数段固定为 `90–100%`、`80–<90%`、
`70–<80%`、`60–<70%`、`<60%`。统计统一使用“得分 ÷ 满分”，因此可以正确处理
不同满分制。当前没有独立姓名/学号字段，学生暂以原始提交文件名识别。

导出文件是点击时的数据快照，不会保存在服务器；成绩修改后应重新导出。导出只
包含教师确认后的最终结果，不会包含待审阅建议、失败记录、OCR 原文、代码或 PDF。

常见错误：

- “该题目暂无已审阅成绩”：先进入评分工作台，由教师确认至少一份最终成绩。
- “请输入大于 0 且不超过 100 的数值”：修正本次及格线后重试。
- 下载失败或超时：确认后端仍在运行，再查看后端日志；导出不会改变任何成绩数据，
  可以安全重试。

Excel 生成功能使用 `XlsxWriter==3.2.9`，已同时锁定在生产和开发依赖文件中。
依赖更新及安装命令见下方“更新后端依赖”，功能验证可运行：

```bash
cd backend
pytest -q tests/test_result_export.py tests/test_result_export_api.py

cd ../frontend
npm run test:run -- src/api/__tests__/resultExport.test.tsx \
  src/pages/__tests__/QuestionsPage.test.tsx
```

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
ruff check app tests

# 运行测试
pytest -q
```

### 更新后端依赖

直接依赖在 `backend/pyproject.toml` 中固定版本，完整依赖图与哈希由
`backend/uv.lock` 锁定（uv 管理，脚本与 CI 使用 `uv sync --frozen`）：

```bash
cd backend
uv lock            # 修改 pyproject.toml 后刷新锁文件
uv sync            # 按锁文件同步 .venv
```

前端依赖由 `package-lock.json` 精确锁定，使用 `npm ci` 安装。

### PDF 存储与去重

PDF 按内容 SHA-256 保存于 `uploads/documents/<前两位>/<完整哈希>.pdf`。同名不同内容
仍是不同文件；内容相同的题目、作业或替换版本共享一个实体文件，原始文件名仍在数据库
中保留。历史 PDF 永久保留，只有记录删除且没有其他引用时才删除实体文件。

已有数据迁移前请备份数据库和 `backend/uploads`。先执行 dry-run：

```bash
./scripts/migrate-document-storage --report storage-migration.json
```

确认报告后再执行写入和旧 Agent 代码产物清理：

```bash
./scripts/migrate-document-storage --apply --clean-legacy-artifacts \
  --report storage-migration.json
```

迁移按 100 条记录分批、按 1MB 流式读取，可在中断后重复执行；路径越界、文件缺失、
读取失败或内容哈希冲突都会停止并报告具体路径。

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

## 评分并发与版本更新

人工确认接口 `POST /api/submissions/{id}/finalize` 必填 `expected_revision`，值为教师所见建议的
`grading_revision`。版本已更新时返回 409，不写最终成绩；缺少版本返回 422。前后端需同步更新并
重新启动 API 与批改 worker（`grading_worker`）。待复核页面持续接收评分及独立复核更新，人工草稿保留，
教师载入最新建议后重新复核。

批改 worker 重启后，恢复扫描会按统一策略处理失效所有权：取消中 → 已取消、检查点驻留保持、
已评分 → 完成、重试耗尽 → 失败、其余重新排队。无需手工修改生产记录。真实并发测试使用现有
`RUN_POSTGRES_TESTS=1` 与 `TEST_DATABASE_URL` 机制，仅允许数据库名称以 `_test` 结尾；
运行后端完整 pytest 时启用该环境，不能使用生产数据库代替。
