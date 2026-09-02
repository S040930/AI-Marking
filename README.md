# AI 作业批改系统

基于 OCR 与编程助手 MCP 的作业批改辅助系统。系统集成 PaddleOCR-VL 文档解析；
评分由本机编程助手（Codex 等）通过 MCP 完成并保存可审计建议，教师最终在网页确认。

## 主要功能

- **PDF 上传**：题目 PDF 通过专门的题目上传页批量上传（拖拽/选择，单文件 ≤50MB）并直接进入 OCR 链路；学生作业 PDF 由编程助手通过本地 MCP 提交。
- **独立题目库**：题目只需上传并 OCR 一次，后续可直接复用于多份学生作业。
- **编程助手 MCP 评分**：在任意支持本地 STDIO MCP 的编程助手中拖入学生 PDF 后自动选题、上传、等待 OCR、读取完整上下文并保存评分建议；最终成绩仍由教师在网页确认。
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
| 评分 | 本机编程助手 MCP（STDIO，评分在客户端完成） |

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

脚本会自动安装缺失依赖、执行数据库迁移、确认 8000 端口没有旧服务，并同时
启动前端、后端和任务 worker。按 `Ctrl+C` 可一起关闭三个进程。

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

脚本固定启动一个 API 进程和一个任务 worker；`WORKERS` 只能为 `1`，更大的值会
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

# 安装精确锁定的依赖(含开发工具)
pip install --require-hashes -r requirements-dev.txt

# 校验 MCP 运行时与后端契约一致(必须为 2.0.0)
python -c 'import importlib.metadata; assert importlib.metadata.version("mcp") == "2.0.0"'

# 配置环境变量
cp .env.example .env
# 编辑 .env 填写数据库连接等

# 执行数据库迁移
alembic upgrade head

# 启动开发服务器
uvicorn app.main:app --reload
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
| `MCP_MAX_GRADING_CONTEXT_CHARS` | MCP 评分上下文总字符硬上限；超过后需拆分作业或缩减提交内容 | `200000` |
| `ACCESS_TOKEN` | 全站访问令牌；留空关闭鉴权，设置后网页、管理 API 与 MCP 均需认证 | 空 |
| `MAX_SSE_CLIENTS` | PostgreSQL SSE LISTEN 连接上限 | `16` |
## 代码执行

后端不编译、执行或上传学生代码产物，也不承担学生代码的 CPU、内存、进程或磁盘资源。
编程助手在当前教师任务中把同题源码复制到独立临时目录后尝试运行，不修改原文件、不申请提权、
不开放网络。运行失败、超时或本机缺少语言环境时由编程助手在对话中明确披露，但不阻止静态评分；
运行输出只存在于当前对话，不作为服务端证据。后端仅持久化源码、SHA-256、评分建议和人工确认。

## 连接编程助手 MCP

在所用编程助手的 MCP 配置中新增一个本地 STDIO 服务，填写项目启动器的绝对路径：

```json
{
  "mcpServers": {
    "ai-marking": {
      "command": "/绝对路径/AI-Marking/scripts/run-ai-marking-mcp",
      "args": ["可选的客户端名称"]
    }
  }
}
```

上面是常见客户端的配置形状；若客户端字段名不同，只需填写同一条 `command` 和可选 `args`。`args` 可省略；如填写请使用小写客户端名称，省略时评分来源记录为“外部编程助手”。MCP 和 FastAPI 只绑定本机 loopback；若 `ACCESS_TOKEN` 非空，STDIO MCP 会从 `backend/.env` 读取同一令牌并以 Bearer 认证，网页则通过登录页建立 httpOnly Cookie 会话。完成配置后运行 `./start.sh`，再在编程助手新任务中拖入一份报告 PDF
和可选的多语言代码文件并输入：

```text
使用 AI-Marking 批改这份作业
```

编程助手会先只读预检题目和代码映射；只有预检通过后才上传。上传后由同一个打开作业工具通过服务端长轮询等待 OCR 完成（PostgreSQL NOTIFY 即时唤醒，最多等待 5 分钟），
再以不透明续页令牌完整读取评分包、完成必要的人工一致性确认、双遍自检并保存建议，
最后返回 `http://localhost:5173/review/{id}` 复核链接。若题目还没有可信 rubric，评分包会返回 `needs_rubric`，编程助手先从题目 OCR 提取评分标准并调用保存工具，再由服务端确定性校验后重新打开。每题可提交一个入口和题目要求的同题辅助源码/头文件。代码运行失败或本机缺少语言环境时由编程助手在对话中披露，但不阻止静态评分。题目不唯一、文件缺失或映射含糊时会在上传前询问。OCR 失败时会返回
原始错误和网页重试地址。MCP 不能确认最终成绩，
教师必须在网页点击确认。

完整工具契约和安全边界见
[docs/architecture/mcp-grading.md](docs/architecture/mcp-grading.md)。

## 持久化任务 worker

首次题目 OCR、题目新版 OCR 和作业 OCR 不在 FastAPI 请求进程中执行。上传接口在同一数据库事务
内创建业务记录和 `background_jobs` 任务，独立 worker 再从 PostgreSQL 原子
领取任务：

```bash
cd backend
.venv/bin/python -m app.worker
```

`start.sh` 与 `start.prod.sh` 已自动启动该 worker。生产脚本固定一个 API 进程和
一个任务 worker，通过 `TASK_CONCURRENCY` 控制 OCR 并发（默认 2）。
进程异常退出后，运行中任务会在租约到期后被重新领取；执行容量满时新任务
保持 `pending` 排队，不再返回队列繁忙 503。

上传支持单个最大 50 MB 的 PDF；编程助手代码联动最多 20 个代码文件、总计 100 MB，单文件 20 MB。代码只在当前编程助手任务的临时目录中尝试运行，不安装学生依赖、不联网。OCR 会在单次任务内对超时、限流和服务端错误进行最多 3 次短暂重试。仍失败
时页面会保留错误原因；题目可重新上传 PDF，学生作业可在原记录上使用已保存的 PDF
重试，或由编程助手重新提交。编程助手只保存待确认评分，教师点击确认后才写入最终结果。

## 使用流程

### 使用编程助手批改（推荐的助手入口）

1. 先在网页题目库准备一个 OCR 已完成的题目。
2. 在编程助手中拖入一份报告 PDF；若作业包含代码，再同时拖入各题入口及辅助源码/头文件，输入“使用 AI-Marking 批改这份作业”。
3. 题目不唯一或代码文件映射不清时先选择/补充；其余 OCR、本地运行尝试、人工报告—代码一致性核验、评分与保存由同一助手任务完成。含代码作业评分前，编程助手会要求使用者回答“已检查且一致”，或回答“已检查且存在不一致”并用自由文字说明差异；尚未检查、含糊回答或未回答时会暂停评分。运行输出和说明仅用于当前对话，不写入作业审计记录。
4. 打开编程助手返回的复核链接，在网页确认最终成绩。

学生作业统一由编程助手提交，网页端不提供学生作业上传入口；评分来源按客户端配置中的名称显示。

### 首次批改某个题目

1. 进入“题目库”上传 PDF 题目。
2. 等待题目 OCR 状态变为“可使用”。
3. 在编程助手中拖入该题目对应的学生报告 PDF（含代码作业同时提供代码文件），输入“使用 AI-Marking 批改这份作业”。
4. 评分建议保存后，在 Review Page 查看评分详情并确认最终评分。

### 继续批改同一题目

在编程助手中继续提交同一题目的新学生作业即可，无需重复上传或识别题目。

### 失败后重新批改

失败记录可在 Review Page 使用已保存的 PDF 重新入队。重试
沿用原 submission ID，并清空旧 OCR 与评分建议。若原 PDF 已
被定期清理，需要由编程助手重新提交。

### 批改其他题目

在题目上传页（题目库 →「上传题目」）批量上传 PDF 题目；OCR 完成后即可在编程助手中提交该题目的学生作业。

> 上传新版会先进入后台 OCR 队列，处理期间题目暂时冻结。新版成功后才清理
> 关联的终态批改记录并原子切换；新版失败时旧题目继续可用。若仍有批改任务
> 正在处理，系统会拒绝操作。危险操作需要输入完整题目名称确认。

## 常见启动问题

- **数据库迁移失败**：检查 `DATABASE_URL` 中的主机、端口、用户名、密码和
  数据库名是否与实际配置一致。
- **Connection refused**：通常是 PostgreSQL 未启动或端口写错，例如误将
  `5555` 写成 `555`。
- **Port in use**：使用 `lsof -nP -iTCP:<端口> -sTCP:LISTEN` 查看占用端口
  的进程；`start.sh` 会拒绝复用已占用的 8000 端口，避免 MCP 误连其他服务或
  旧 AI-Marking 进程。已有 PostgreSQL 正常运行时可直接使用该数据库实例。
- **MCP 显示已连接但工具 404**：通常是 8000 端口指向其他应用或旧后端。停止占用
  8000 端口的错误进程后重新运行 `./start.sh`，再重启编程助手。
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

当前最新迁移包含独立题目库。旧迁移文件必须保留，用于新环境建库、升级和
回滚。

如需清空题目与作业数据，同时保留系统配置、表结构及迁移版本：

```sql
TRUNCATE TABLE submissions, questions
RESTART IDENTITY CASCADE;
```

该操作不可恢复，且不会删除 `backend/uploads` 中残留的 PDF 文件。

## API 配置说明

OCR 与评分标准等**业务配置**通过前端设置页面(`/settings`)管理,存储在数据库中,无需修改 `.env` 或重启服务。

配置项包括:

- **OCR**:PaddleOCR-VL API URL、Access Token
- **评分标准**:题目可信提取 rubric 优先，其次使用配置项目中的结构化 rubric（条目、满分、说明），最后回退内置默认
- **MCP 自检开关**:是否要求编程助手在保存建议前完成第二遍反向自检（默认开启）

## 批改流程

题目入库时先独立完成一次 OCR。每份学生作业使用缓存的题目文本，避免
重复调用题目 OCR。作业 OCR 完成后进入 `awaiting_mcp`，由本机编程助手通过
MCP 打开评分包：

```text
题目库 OCR（仅首次）
        ↓
学生作业 OCR → awaiting_mcp → MCP 评分（客户端双遍自检）→ ready_for_review
                                                                   ↓
                                 （可选）另一个编程助手任务独立复核建议
                                                                   ↓
                                            教师在网页人工复核并确认最终成绩
```

后端不调用任何 LLM。编程助手保存的是待确认建议；教师确认后写入最终结果，
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

直接依赖在 `backend/pyproject.toml` 中固定版本，完整间接依赖与文件哈希分别
保存在生产和开发锁文件中：

```bash
cd backend
.venv/bin/pip-compile pyproject.toml \
  --output-file requirements-prod.txt \
  --generate-hashes --allow-unsafe --strip-extras
.venv/bin/pip-compile pyproject.toml \
  --extra dev \
  --output-file requirements-dev.txt \
  --generate-hashes --allow-unsafe --strip-extras
```

安装时使用：

```bash
.venv/bin/pip install --require-hashes -r requirements-dev.txt
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
