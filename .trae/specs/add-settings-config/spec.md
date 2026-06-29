# 页面配置 API Key 功能 Spec

## Why
当前 OCR 与 LLM 服务的 API Key、endpoint、rubric 等配置硬编码在后端 `.env` 与 `prompt.py` 中,修改需要重启服务并接触服务器。用户希望**在页面中定义接入的模型 API**,使配置可视化、可即时修改、可持久化。这是 MVP 走向可用与可演示的关键一步,也是后续多用户 / 多模型场景的基础。

## What Changes
- 后端:新增 `SystemConfig` 模型(key-value 表,通用配置存储)+ Alembic 迁移
- 后端:新增 `app/api/config.py`,提供 `GET /api/config`(读取全部配置项)与 `PUT /api/config`(批量 upsert 配置项)
- 后端:新增 `app/services/config.py` 配置读取服务,提供 `get_config_dict(db) -> dict` 与按需读取的辅助函数,OCR/LLM/prompt 服务改为从该服务读取配置
- 后端:修改 `app/services/ocr.py`,从 DB 读取 `baidu_ocr_api_key`、`baidu_ocr_secret_key`,移除对 `settings` 的依赖
- 后端:修改 `app/services/llm.py`,从 DB 读取 `doubao_api_key`、`doubao_base_url`、`doubao_model`,移除对 `settings` 的依赖
- 后端:修改 `app/core/prompt.py`,支持接收外部 rubric;`build_user_prompt(ocr_text, rubric=None)`,rubric 为空时回退到默认 RUBRIC
- 后端:修改 `app/services/marking.py`,在流水线开始处读取一次配置,传给 OCR/LLM 调用,避免每阶段重复读 DB
- 后端:在 `main.py` 注册 config router
- 后端:保留 `.env` 中的 `DATABASE_URL`、`CORS_ORIGINS`、`UPLOAD_DIR`(应用级配置),其余 LLM/OCR 配置从 `settings` 中移除(迁移到 DB)
- 前端:新增 `frontend/src/pages/SettingsPage.tsx`,使用 Ant Design Form 录入 6 项配置(豆包 API Key、豆包 base_url、豆包 model/endpoint、百度 OCR API Key、百度 OCR Secret Key、自定义 Rubric),保存成功后提示
- 前端:新增 `frontend/src/api/config.ts`,提供类型定义、`useConfig()`(GET Query)与 `useUpdateConfig()`(PUT Mutation)
- 前端:在 `MainLayout.tsx` 新增"设置"导航项(Setting 图标)
- 前端:在 `router/index.tsx` 新增 `/settings` 路由

## Impact
- Affected specs: `implement-marking-pipeline`(其 OCR/LLM/prompt 实现需重构,Task 10-11 验证依赖本 spec 完成)
- Affected code:
  - 新增后端:`app/models/system_config.py`、`app/schemas/system_config.py`、`app/api/config.py`、`app/services/config.py`
  - 新增迁移:`alembic/versions/xxx_create_system_config.py`
  - 修改后端:`app/models/__init__.py`、`app/db/base.py`、`app/services/{ocr,llm,marking}.py`、`app/core/{config,prompt}.py`、`app/main.py`
  - 新增前端:`src/pages/SettingsPage.tsx`、`src/api/config.ts`
  - 修改前端:`src/layouts/MainLayout.tsx`、`src/router/index.tsx`

## ADDED Requirements

### Requirement: SystemConfig 数据模型
系统 SHALL 提供 `SystemConfig` 模型,以 key-value 形式存储可配置项。

字段:
- `id: int` 主键
- `key: str` 唯一配置键(如 `doubao_api_key`、`rubric`)
- `value: str` 配置值(大文本,如 rubric 可能较长,使用 `Text` 类型)
- `description: str | None` 配置项描述(供前端展示)
- `updated_at: datetime` 最后修改时间

#### Scenario: 表已创建
- **WHEN** 执行 `alembic upgrade head`
- **THEN** `system_config` 表存在且包含上述字段
- **AND** `key` 字段有唯一约束

### Requirement: 配置读取服务
系统 SHALL 提供配置读取服务,从 DB 一次性读取所有配置并以字典形式返回。

#### Scenario: 读取全部配置
- **WHEN** 调用 `get_config_dict(db)`
- **THEN** 返回 `dict[str, str]`,包含表内所有 key-value
- **AND** 不存在的 key 在字典中缺省(由调用方处理默认值)

#### Scenario: 缓存(可选)
- **WHEN** 短时间内多次调用
- **THEN** 可使用进程内缓存(如 5 秒 TTL)以降低 DB 压力
- **AND** PUT 配置后应使缓存失效

### Requirement: 配置查询接口
系统 SHALL 提供 `GET /api/config` 接口返回当前所有配置(供前端展示)。

#### Scenario: 返回配置
- **WHEN** `GET /api/config`
- **THEN** 返回结构化 JSON,固定字段:`doubao_api_key`、`doubao_base_url`、`doubao_model`、`baidu_ocr_api_key`、`baidu_ocr_secret_key`、`rubric`
- **AND** 未配置的字段返回空字符串 `""`
- **AND** 同时返回 `description` 字段(可选,前端用于展示提示)

#### Scenario: 敏感字段处理
- **WHEN** 返回 `doubao_api_key`、`baidu_ocr_api_key`、`baidu_ocr_secret_key` 等敏感字段
- **THEN** 返回真实值(供前端编辑回填,MVP 不做掩码,前端通过密码输入框隐藏)
- **AND** 文档注明该接口仅内部使用(MVP 不做鉴权)

### Requirement: 配置更新接口
系统 SHALL 提供 `PUT /api/config` 接口批量更新配置。

#### Scenario: 更新成功
- **WHEN** 客户端 PUT JSON,含任意子集字段(如 `{"doubao_api_key": "sk-xxx", "rubric": "..."}`)
- **THEN** 服务端对每个提供的字段执行 upsert(存在则更新,不存在则插入)
- **AND** 未提供的字段保持不变
- **AND** 更新 `updated_at` 时间戳
- **AND** 失效配置缓存
- **AND** 返回更新后的完整配置(同 GET 结构)

#### Scenario: 字段校验
- **WHEN** 字段名为未声明的 key
- **THEN** 返回 400 与 `{"detail": "未知配置项: <key>"}`
- **AND** 不执行任何写入

#### Scenario: 空值允许
- **WHEN** 某字段值为空字符串
- **THEN** 允许写入(表示清空该配置)

### Requirement: OCR 服务从 DB 读取配置
系统 SHALL 修改 OCR 服务,从数据库读取百度 OCR 配置而非 `settings`。

#### Scenario: 配置完整
- **WHEN** DB 中存在 `baidu_ocr_api_key` 与 `baidu_ocr_secret_key` 且非空
- **THEN** 使用 DB 中的值换取 access_token 并执行 OCR

#### Scenario: 配置缺失
- **WHEN** DB 中 `baidu_ocr_api_key` 或 `baidu_ocr_secret_key` 为空或不存在
- **THEN** 抛出 `OCRError("百度 OCR API Key 未配置,请在设置页填写")`
- **AND** 流水线状态转为 failed,error_message 写入上述提示

### Requirement: LLM 服务从 DB 读取配置
系统 SHALL 修改 LLM 服务,从数据库读取豆包配置而非 `settings`。

#### Scenario: 配置完整
- **WHEN** DB 中存在 `doubao_api_key`、`doubao_base_url`、`doubao_model` 且非空
- **THEN** 使用 DB 中的值创建 OpenAI client 并调用豆包

#### Scenario: 配置缺失
- **WHEN** `doubao_api_key` 为空
- **THEN** 抛出 `LLMError("豆包 API Key 未配置,请在设置页填写")`

#### Scenario: base_url / model 回退
- **WHEN** `doubao_base_url` 或 `doubao_model` 为空
- **THEN** 使用代码中的默认值(`https://ark.cn-beijing.volces.com/api/v3`、`doubao-pro-32k`)

### Requirement: Rubric 自定义
系统 SHALL 支持从 DB 读取自定义 rubric,未配置时回退到默认 rubric。

#### Scenario: 使用自定义 rubric
- **WHEN** DB 中 `rubric` 非空
- **THEN** `build_user_prompt(ocr_text, rubric=<自定义值>)` 使用该 rubric 拼接 prompt

#### Scenario: 回退默认
- **WHEN** DB 中 `rubric` 为空或不存在
- **THEN** 使用 `prompt.py` 中硬编码的默认 `RUBRIC`

### Requirement: 流水线读取配置
系统 SHALL 在批改流水线开始时读取一次配置并传递给 OCR/LLM 调用。

#### Scenario: 单次读取
- **WHEN** `run_marking_pipeline(submission_id)` 开始执行
- **THEN** 调用 `get_config_dict(db)` 获取配置
- **AND** 将配置参数传给 `ocr_pdf` 与 `mark_submission`
- **AND** 不在 OCR/LLM 服务内部重复读 DB(由参数注入)

### Requirement: 前端设置页
系统 SHALL 提供设置页面 `/settings`,可录入并保存 6 项配置。

#### Scenario: 进入页面加载
- **WHEN** 用户访问 `/settings`
- **THEN** 调用 `GET /api/config` 获取当前配置
- **AND** 用 Ant Design Form 回填表单
- **AND** 敏感字段(API Key / Secret Key)使用 `Input.Password` 隐藏

#### Scenario: 表单字段
- **WHEN** 渲染表单
- **THEN** 包含以下字段:
  - 豆包 API Key(`Input.Password`)
  - 豆包 Base URL(`Input`,默认占位 `https://ark.cn-beijing.volces.com/api/v3`)
  - 豆包 Model / Endpoint ID(`Input`,占位 `doubao-pro-32k`)
  - 百度 OCR API Key(`Input.Password`)
  - 百度 OCR Secret Key(`Input.Password`)
  - 自定义 Rubric(`Input.TextArea`,多行,占位为默认 rubric 摘要)
- **AND** 使用 Card 分组:豆包 LLM 配置、百度 OCR 配置、评分标准

#### Scenario: 保存配置
- **WHEN** 用户点击"保存配置"按钮
- **THEN** 调用 `PUT /api/config` 提交表单
- **AND** 提交中按钮 loading
- **AND** 成功后 `message.success("配置已保存")`
- **AND** 失败时 `message.error(<错误详情>)`

#### Scenario: 重置默认
- **WHEN** 用户点击"重置 Rubric 为默认"按钮(可选)
- **THEN** 将 rubric 字段清空(后端检测到空值时使用默认)

### Requirement: 前端导航与路由
系统 SHALL 在导航栏新增"设置"入口,并在路由中注册。

#### Scenario: 导航项
- **WHEN** 渲染 MainLayout 的 Sider Menu
- **THEN** 包含"设置"项(图标 `SettingOutlined`)
- **AND** 点击后跳转 `/settings`

#### Scenario: 路由注册
- **WHEN** 访问 `/settings`
- **THEN** 渲染 `SettingsPage`,布局复用 `MainLayout`

## MODIFIED Requirements

### Requirement: implement-marking-pipeline 的 OCR / LLM / Prompt 配置来源
**原实现**:OCR 从 `settings.BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY` 读取;LLM 从 `settings.DOUBAO_API_KEY` / `DOUBAO_BASE_URL` / `DOUBAO_MODEL` 读取;rubric 硬编码在 `prompt.py`。

**新实现**:OCR / LLM 改为接收配置参数(由 `marking.py` 从 DB 读取后注入);rubric 由 `marking.py` 从 DB 读取后传给 `build_user_prompt(ocr_text, rubric)`。`settings` 仅保留 `DATABASE_URL`、`CORS_ORIGINS`、`UPLOAD_DIR` 三项应用级配置。
