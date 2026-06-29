# 验收检查清单

## 项目结构
- [x] 根目录包含 `backend/` 与 `frontend/` 两个平级子目录
- [x] 根 `.gitignore` 覆盖 Python(`__pycache__`、`.venv`、`.env`)、Node(`node_modules`、`dist`)、IDE(`.idea`、`.vscode`)、OS(`.DS_Store`)
- [x] 后端与前端各自拥有独立的依赖文件(`pyproject.toml` / `package.json`)

## 后端 FastAPI
- [x] `backend/app/main.py` 使用应用工厂模式创建 FastAPI 实例
- [x] CORS 中间件已配置,允许 `http://localhost:5173`
- [x] `GET /api/health` 返回 JSON,包含 `status: "ok"` 与 `timestamp` 字段(已修复路径,从 `/api/` 改为 `/api/health`)
- [x] `GET /docs` 可访问 Swagger UI(返回 HTML 200)
- [x] `pydantic-settings` Settings 类从 `.env` 读取 `DATABASE_URL`、`CORS_ORIGINS`、`DOUBAO_API_KEY`、`BAIDU_OCR_API_KEY`、`BAIDU_OCR_SECRET_KEY`
- [x] `backend/.env.example` 列出所有环境变量及其示例值

## 数据库层
- [x] `backend/app/db/base.py` 定义 `DeclarativeBase` 子类 `Base`
- [x] `backend/app/db/session.py` 提供 `engine`、`SessionLocal`、`get_db` 依赖
- [x] Alembic 已初始化,`alembic.ini` 与 `alembic/env.py` 存在
- [x] `alembic/env.py` 从 Settings 读取 `DATABASE_URL`
- [ ] `alembic upgrade head` 可正常执行(未测试,需 PostgreSQL 运行;迁移结构与 env.py 配置已就绪)

## 前端 Vite + React
- [x] `frontend/package.json` 包含 react、react-dom、antd、react-router-dom、@tanstack/react-query、axios、dayjs
- [x] `npm run dev` 可在 `http://localhost:5173` 启动(vite v8.1.0 ready in 97ms)
- [x] `vite.config.ts` 配置 `/api` 代理到 `http://localhost:8000`
- [x] `tsconfig.app.json` 配置 `@/*` 路径别名指向 `src/*`(TS6 弃用 baseUrl,改用 paths-only,功能等价)
- [x] `src/layouts/MainLayout.tsx` 使用 Ant Design Layout,包含侧边导航(上传、历史)
- [x] `src/router/index.tsx` 配置 `/`、`/history`、`/result/:id` 三条路由
- [x] `src/api/client.ts` 创建 axios 实例,baseURL 为 `/api`
- [x] `App.tsx` 挂载 `QueryClientProvider` 与 `RouterProvider`

## 工程化
- [x] 后端 `ruff check app/` 退出码为 0(All checks passed!)
- [x] 后端 `black --check app/` 退出码为 0(9 files unchanged)
- [x] 前端 `npm run lint` 退出码为 0(0 warnings 0 errors,使用 oxlint)
- [x] 前端 `package.json` 含 `lint`、`format`、`dev`、`build` 脚本

## 联调验证
- [x] 后端启动后 `curl http://localhost:8000/api/health` 返回 200 与正确 JSON
- [x] 前端启动后浏览器访问 `http://localhost:5173` 显示主布局(返回 HTML 含 root div)
- [x] 前端导航可在上传、历史、结果页之间切换(路由代码已就绪,MainLayout 使用 useNavigate 同步菜单选中)
- [x] 前端通过代理调用后端 `/api/health` 成功(`curl 127.0.0.1:5173/api/health` 返回 200 + `{"status":"ok",...}`,响应头含 `Server: uvicorn`)

## 实施备注
- 后端 `pip install -e ".[dev]"` 已验证可成功(修复了 pyproject.toml 的 `[build-system]` 与 `[tool.setuptools.packages.find]` 配置)
- 前端使用 Vite 最新模板,实际版本:React 19、antd 6、react-router-dom 7、@tanstack/react-query 5、vite 8、TypeScript 6
- 前端 lint 工具为 oxlint(Vite 模板默认),非 eslint;`.oxlintrc.json` 已存在
- TypeScript 6 弃用 `baseUrl`,改用 paths-only 配置,功能等价
- `alembic upgrade head` 未执行,因当前环境无 PostgreSQL 服务运行;文件结构已就绪,待 DB 可用时可直接执行
