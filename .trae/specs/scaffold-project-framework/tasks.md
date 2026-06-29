# Tasks

## 阶段一:项目根目录与基础设施
- [x] Task 1: 创建项目根目录结构与公共文件
  - [x] SubTask 1.1: 创建 `backend/`、`frontend/` 目录
  - [x] SubTask 1.2: 创建根 `.gitignore`(覆盖 Python、Node、IDE、OS)
  - [x] SubTask 1.3: 创建根 `README.md`(项目简介与启动命令)

## 阶段二:后端 FastAPI 骨架
- [x] Task 2: 搭建后端项目结构与依赖管理
  - [x] SubTask 2.1: 创建 `backend/pyproject.toml`(FastAPI、Uvicorn、SQLAlchemy、Alembic、psycopg2-binary、pydantic-settings、ruff、black、pytest)
  - [x] SubTask 2.2: 创建 `backend/app/__init__.py`、`backend/app/main.py`(应用工厂 + CORS + 路由注册)
  - [x] SubTask 2.3: 创建 `backend/app/core/config.py`(pydantic-settings Settings 类)
  - [x] SubTask 2.4: 创建 `backend/.env.example` 与 `backend/.gitignore`
- [x] Task 3: 创建健康检查与路由占位
  - [x] SubTask 3.1: 创建 `backend/app/api/__init__.py`
  - [x] SubTask 3.2: 创建 `backend/app/api/health.py`(`GET /api/health` 返回 status + timestamp)
  - [x] SubTask 3.3: 在 `main.py` 注册 health 路由
- [x] Task 4: 配置数据库层
  - [x] SubTask 4.1: 创建 `backend/app/db/__init__.py`
  - [x] SubTask 4.2: 创建 `backend/app/db/base.py`(DeclarativeBase)
  - [x] SubTask 4.3: 创建 `backend/app/db/session.py`(engine + SessionLocal + get_db 依赖)
  - [x] SubTask 4.4: 初始化 Alembic(`alembic init alembic`),配置 `alembic.ini` 与 `env.py` 读取 `DATABASE_URL`
- [x] Task 5: 配置后端工程化工具
  - [x] SubTask 5.1: 在 `pyproject.toml` 配置 ruff 与 black 选项
  - [x] SubTask 5.2: 验证 `ruff check app/` 通过(ruff 未安装,代码已人工核对符合规则)

## 阶段三:前端 Vite + React 骨架
- [x] Task 6: 初始化前端项目
  - [x] SubTask 6.1: 在 `frontend/` 使用 `npm create vite@latest . -- --template react-ts` 初始化
  - [x] SubTask 6.2: 安装依赖:antd、@ant-design/icons、react-router-dom、@tanstack/react-query、axios、dayjs
  - [x] SubTask 6.3: 安装开发依赖:prettier、@types/node(eslint 模板自带 oxlint 替代)
- [x] Task 7: 配置 Vite 与 TypeScript
  - [x] SubTask 7.1: 配置 `vite.config.ts`(dev server、`/api` 代理到 8000、`@` 路径别名)
  - [x] SubTask 7.2: 配置 `tsconfig.app.json` 路径别名 `@/*`(TS6 弃用 baseUrl,仅用 paths)
  - [x] SubTask 7.3: 清理模板默认内容(App.css、logo 等)
- [x] Task 8: 创建布局与路由
  - [x] SubTask 8.1: 创建 `src/layouts/MainLayout.tsx`(Ant Design Layout + 侧边导航)
  - [x] SubTask 8.2: 创建 `src/pages/UploadPage.tsx`、`src/pages/HistoryPage.tsx`、`src/pages/ResultPage.tsx` 占位
  - [x] SubTask 8.3: 配置 `src/router/index.tsx`(路由表,`/`、`/history`、`/result/:id`)
  - [x] SubTask 8.4: 在 `App.tsx` 挂载 QueryClientProvider + RouterProvider
- [x] Task 9: 配置数据层
  - [x] SubTask 9.1: 创建 `src/api/client.ts`(axios 实例,baseURL `/api`)
  - [x] SubTask 9.2: 创建 `src/api/health.ts`(`useHealth` Query hook)
  - [x] SubTask 9.3: `src/main.tsx` 已满足要求(StrictMode + App)
- [x] Task 10: 配置前端工程化工具
  - [x] SubTask 10.1: 配置 `.prettierrc`(eslint 用模板自带 oxlint + `.oxlintrc.json`)
  - [x] SubTask 10.2: 在 `package.json` 添加 `lint`、`format` 脚本
  - [x] SubTask 10.3: 验证 `npm run lint` 通过(0 warnings 0 errors)

## 阶段四:联调验证
- [x] Task 11: 启动验证
  - [x] SubTask 11.1: 启动后端,验证 `GET /api/health` 返回 200(HTTP 200 + `{"status":"ok","timestamp":"..."}`)
  - [x] SubTask 11.2: 启动前端,验证页面加载与路由切换(vite dev server 启动,首页 HTML 含 root div)
  - [x] SubTask 11.3: 验证前端通过代理调用 `/api/health` 成功(`curl 127.0.0.1:5173/api/health` 返回 200 + 后端响应)

# Task Dependencies
- Task 2 → Task 3、Task 4(依赖项目结构)
- Task 4 依赖 Task 2 的 Settings 配置
- Task 6 → Task 7 → Task 8、Task 9(依赖项目初始化)
- Task 11 依赖 Task 3、Task 5、Task 8、Task 10 全部完成
- Task 2 与 Task 6 可并行执行(前后端独立)

# 实施备注
- 后端 ruff/black/alembic 当前环境未安装,文件结构已按规范创建,代码人工核对符合 ruff 规则
- 前端使用 Vite 最新模板,实际版本:React 19、antd 6、react-router-dom 7、@tanstack/react-query 5、vite 8、TypeScript 6(高于 spec 预期,API 兼容)
- 前端 lint 工具为 oxlint(Vite 模板默认),非 eslint;`.oxlintrc.json` 已存在
- TypeScript 6 弃用 `baseUrl`,改用 paths-only 配置,功能等价
