# 项目框架搭建 Spec

## Why
AI 作业批改系统 MVP 需要一个可运行的前后端骨架,作为后续 OCR、LLM、上传流程等业务功能开发的基础。本阶段只搭建框架,不实现具体业务逻辑,确保前后端均可独立启动并通过健康检查。

## What Changes
- 创建项目根目录结构(monorepo 风格,`backend/` + `frontend/` 平级)
- 搭建后端 FastAPI 骨架:项目结构、依赖管理、配置系统、健康检查端点
- 搭建数据库层:SQLAlchemy 2.0 引擎、Session、Base 声明、Alembic 迁移初始化
- 搭建前端 Vite + React + TypeScript 骨架:依赖、配置、Ant Design 接入
- 配置前端路由(Router v6)、数据层(TanStack Query + axios 实例)
- 创建基础布局组件与占位页面(上传页、历史页、结果页)
- 配置工程化工具:后端 Ruff + Black,前端 ESLint + Prettier
- 配置 `.env.example` 与跨域(CORS)策略

## Impact
- Affected specs: 无(首次搭建)
- Affected code: 全新项目骨架,不涉及已有代码

## ADDED Requirements

### Requirement: 项目目录结构
系统 SHALL 采用 monorepo 结构,根目录包含 `backend/` 与 `frontend/` 两个平级子项目,各自独立管理依赖。

#### Scenario: 目录结构验证
- **WHEN** 开发者进入项目根目录
- **THEN** 应能看到 `backend/`、`frontend/`、`.gitignore`、`README.md`(可选)、`.trae/specs/` 等条目
- **AND** `backend/` 与 `frontend/` 各自包含独立的项目文件

### Requirement: 后端 FastAPI 骨架
系统 SHALL 提供可启动的 FastAPI 应用,包含应用工厂、配置加载、健康检查端点。

#### Scenario: 后端启动并响应健康检查
- **WHEN** 执行 `uvicorn app.main:app --reload`
- **THEN** 服务在 `http://localhost:8000` 启动
- **AND** `GET /api/health` 返回 `{"status": "ok"}` 与时间戳
- **AND** `GET /docs` 可访问 Swagger UI

### Requirement: 配置管理系统
系统 SHALL 通过 `pydantic-settings` 从 `.env` 加载配置,包含数据库 URL、CORS 来源、OCR/LLM API Key 占位。

#### Scenario: 配置加载
- **WHEN** 提供 `.env` 文件
- **THEN** 应用启动时读取配置
- **AND** 未设置必需项时给出明确错误提示

### Requirement: 数据库层骨架
系统 SHALL 配置 SQLAlchemy 2.0 引擎、Session 工厂、Base 声明类,并提供 Alembic 迁移初始化。

#### Scenario: 数据库连接
- **WHEN** PostgreSQL 服务可用且 `.env` 中 `DATABASE_URL` 正确
- **THEN** 应用启动时能成功建立连接
- **AND** `alembic upgrade head` 可执行(初始空迁移)

### Requirement: 前端 Vite + React 骨架
系统 SHALL 提供可启动的 Vite + React + TypeScript 应用,集成 Ant Design。

#### Scenario: 前端启动
- **WHEN** 执行 `npm run dev`
- **THEN** 服务在 `http://localhost:5173` 启动
- **AND** 页面显示带 Ant Design Layout 的基础界面
- **AND** 开发代理将 `/api` 转发到 `http://localhost:8000`

### Requirement: 前端路由与数据层
系统 SHALL 配置 React Router v6 与 TanStack Query,提供三个占位页面:上传、历史、结果。

#### Scenario: 路由访问
- **WHEN** 访问 `/`、`/history`、`/result/:id`
- **THEN** 各路由渲染对应占位页面
- **AND** 顶部导航在路由间切换

#### Scenario: API 客户端
- **WHEN** 前端调用 `/api/health`
- **THEN** axios 通过代理转发到后端
- **AND** TanStack Query 缓存返回结果

### Requirement: 工程化工具配置
系统 SHALL 在前后端分别配置代码规范工具:后端 Ruff + Black,前端 ESLint + Prettier。

#### Scenario: 代码检查
- **WHEN** 执行 `ruff check backend/` 与 `npm run lint`
- **THEN** 两条命令均能正常退出(无错即可,允许警告)
