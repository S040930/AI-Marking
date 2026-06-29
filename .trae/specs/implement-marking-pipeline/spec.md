# 核心批改流程实现 Spec

## Why
在已搭好的前后端骨架基础上,实现 MVP 核心业务闭环:用户上传 PDF → 百度云 OCR 解析为文本 → 豆包 LLM 按 rubric 批改 → 结果存库 → 前端展示批改详情与历史记录。这是整个 AI 作业批改系统的最小可演示闭环。

## What Changes
- 后端:数据模型 `Submission`(单表,含 OCR 文本与批改结果,简化 MVP)
- 后端:Alembic 迁移创建表
- 后端:PDF 上传接口 `POST /api/submissions`(保存文件 + 创建记录 + 触发后台任务)
- 后端:百度云 OCR 集成服务 `app/services/ocr.py`(access_token 获取 + PDF/图片识别)
- 后端:豆包 LLM 集成服务 `app/services/llm.py`(OpenAI SDK 兼容调用 + JSON 输出)
- 后端:批改任务编排 `app/services/marking.py`(BackgroundTasks 串联 OCR→LLM)
- 后端:Rubric 与 Prompt 模板 `app/core/prompt.py`(硬编码通用 rubric)
- 后端:查询接口 `GET /api/submissions`(列表)、`GET /api/submissions/{id}`(详情)
- 前端:上传页改造(真正上传 PDF + 提交后跳转结果页)
- 前端:历史页改造(表格列表 + 状态标签 + 轮询刷新)
- 前端:结果页改造(展示 OCR 文本、分数、批改反馈、详细项)
- 前端:API hooks(`useSubmissions`、`useSubmission`、`useUploadSubmission`)

## Impact
- Affected specs: `scaffold-project-framework`(依赖其骨架)
- Affected code:
  - 新增后端:`app/models/`、`app/services/`、`app/schemas/`、`app/api/submissions.py`、`app/core/prompt.py`
  - 新增迁移:`alembic/versions/xxx_create_submissions.py`
  - 修改后端:`app/main.py`(注册新路由)、`app/db/base.py`(导入模型)
  - 修改前端:`src/pages/{Upload,History,Result}Page.tsx`、`src/api/submissions.ts`

## ADDED Requirements

### Requirement: 数据模型
系统 SHALL 提供 `Submission` 模型,单表存储完整批改流程数据。

字段:
- `id: int` 主键
- `original_filename: str` 原始文件名
- `file_path: str` 服务器存储路径
- `status: enum` ∈ {pending, ocr_processing, ocr_done, llm_processing, done, failed}
- `ocr_text: str | None` OCR 解析文本
- `score: float | None` 批改分数
- `feedback: str | None` 总体反馈
- `details: JSON | None` 详细评分项(数组,每项含 criterion/score/comment)
- `error_message: str | None` 失败原因
- `uploaded_at: datetime` 上传时间
- `completed_at: datetime | None` 完成时间

#### Scenario: 表已创建
- **WHEN** 执行 `alembic upgrade head`
- **THEN** `submissions` 表存在且包含上述所有字段
- **AND** `status` 字段使用枚举类型

### Requirement: PDF 上传接口
系统 SHALL 提供 `POST /api/submissions` 接口接收 PDF 文件。

#### Scenario: 上传成功
- **WHEN** 客户端 POST 一个 PDF 文件(multipart/form-data,字段名 `file`,MIME `application/pdf`)
- **THEN** 服务端将文件保存到 `UPLOAD_DIR`(以 UUID 命名)
- **AND** 创建 `Submission` 记录(status=pending)
- **AND** 触发 BackgroundTask 开始批改流程
- **AND** 返回 201 与 submission JSON(含 id、status)

#### Scenario: 上传非 PDF
- **WHEN** 客户端 POST 非 PDF 文件
- **THEN** 返回 400 与 `{"detail": "仅支持 PDF 文件"}`

#### Scenario: 未提供文件
- **WHEN** 客户端 POST 无 file 字段
- **THEN** 返回 422 与 FastAPI 默认校验错误

### Requirement: 百度云 OCR 集成
系统 SHALL 通过百度云 OCR API 将 PDF(或其页面渲染图)解析为文本。

#### Scenario: OCR 成功
- **WHEN** 任务调用 OCR 服务处理已保存的 PDF
- **THEN** 使用 `BAIDU_OCR_API_KEY` + `BAIDU_OCR_SECRET_KEY` 获取 access_token
- **AND** 调用百度通用文字识别(高精度版)或 PDF 文字识别接口
- **AND** 返回拼接后的纯文本
- **AND** 更新 Submission: `ocr_text=<文本>`、`status=ocr_done`

#### Scenario: OCR 失败
- **WHEN** 百度 API 返回错误或网络异常
- **THEN** 更新 Submission: `status=failed`、`error_message=<错误描述>`
- **AND** 不继续后续 LLM 步骤

### Requirement: 豆包 LLM 批改集成
系统 SHALL 通过 OpenAI SDK 兼容方式调用豆包 API,基于 rubric 对 OCR 文本进行批改并返回结构化 JSON。

#### Scenario: LLM 批改成功
- **WHEN** 任务调用 LLM 服务,传入 OCR 文本与 rubric prompt
- **THEN** 使用 `DOUBAO_API_KEY` 与 `DOUBAO_BASE_URL` 初始化 OpenAI client
- **AND** 调用 `chat.completions.create`(model=`DOUBAO_MODEL`)
- **AND** 要求返回 JSON:`{"score": float, "feedback": str, "details": [{criterion, score, comment}]}`
- **AND** 解析响应并更新 Submission: `score`、`feedback`、`details`、`status=done`、`completed_at=now`
- **AND** prompt 包含 rubric(评分维度)与 CoT 引导

#### Scenario: LLM 响应解析失败
- **WHEN** LLM 返回非合法 JSON 或字段缺失
- **THEN** 更新 Submission: `status=failed`、`error_message="LLM 响应解析失败: <详情>"`
- **AND** 记录原始响应到日志

### Requirement: 批改任务编排
系统 SHALL 使用 FastAPI BackgroundTasks 串联 OCR 与 LLM 流程,并维护状态机。

#### Scenario: 正常流程
- **WHEN** 上传成功触发 BackgroundTask
- **THEN** 状态流转:pending → ocr_processing → ocr_done → llm_processing → done
- **AND** 每个阶段更新数据库 status
- **AND** 任何阶段失败立即终止并标记 failed

#### Scenario: 并发安全
- **WHEN** 多个上传同时进行
- **THEN** 每个任务独立处理各自的 Submission(无共享状态)

### Requirement: 查询接口
系统 SHALL 提供查询接口供前端获取列表与详情。

#### Scenario: 列表查询
- **WHEN** `GET /api/submissions`
- **THEN** 返回所有 Submission(按 uploaded_at 降序)
- **AND** 每条含 id、original_filename、status、score、uploaded_at、completed_at
- **AND** 不含 ocr_text、details(列表精简)

#### Scenario: 详情查询
- **WHEN** `GET /api/submissions/{id}`
- **THEN** 返回该 Submission 完整字段(含 ocr_text、feedback、details)
- **AND** 不存在时返回 404

### Requirement: 前端上传页
系统 SHALL 提供可真正上传 PDF 的上传页面。

#### Scenario: 选择并上传
- **WHEN** 用户在上传页选择 PDF 并点击"开始批改"
- **THEN** 调用 `POST /api/submissions`
- **AND** 上传中显示 loading
- **AND** 成功后跳转到 `/result/:id`

#### Scenario: 文件类型校验
- **WHEN** 用户选择非 PDF 文件
- **THEN** 前端拦截并提示"仅支持 PDF"
- **AND** 不发起请求

### Requirement: 前端历史页
系统 SHALL 提供历史记录列表页,展示所有提交并实时刷新状态。

#### Scenario: 列表展示
- **WHEN** 用户访问 `/history`
- **THEN** 调用 `GET /api/submissions` 获取列表
- **THEN** 用 Ant Design Table 展示(列:文件名、状态、分数、上传时间、操作)
- **AND** 状态用 Tag 着色(pending/processing=蓝、done=绿、failed=红)

#### Scenario: 状态轮询
- **WHEN** 列表中存在非终态(pending/processing)记录
- **THEN** 每 3 秒自动重新拉取列表
- **AND** 所有记录进入终态(done/failed)后停止轮询

#### Scenario: 查看详情
- **WHEN** 用户点击某行的"查看"
- **THEN** 跳转到 `/result/:id`

### Requirement: 前端结果页
系统 SHALL 提供批改结果展示页,支持未完成时轮询、完成时展示详情。

#### Scenario: 处理中状态
- **WHEN** 访问 `/result/:id` 且 status 非 done/failed
- **THEN** 显示 Ant Design Spin 或 Progress,提示"正在批改中..."
- **AND** 每 2 秒轮询 `GET /api/submissions/{id}`

#### Scenario: 批改完成
- **WHEN** status=done
- **THEN** 展示:文件名、总分、总体反馈(Card)、详细评分项(列表/Table)、OCR 原文(可折叠)
- **AND** 停止轮询

#### Scenario: 批改失败
- **WHEN** status=failed
- **THEN** 展示 Ant Design Result(error 状态)+ error_message
- **AND** 提供返回上传页按钮
- **AND** 停止轮询
