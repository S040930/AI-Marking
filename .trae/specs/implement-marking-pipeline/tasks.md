# Tasks

## 阶段一:后端数据层与模型
- [ ] Task 1: 创建数据模型与迁移
  - [ ] SubTask 1.1: 创建 `backend/app/models/__init__.py` 与 `backend/app/models/submission.py`(SQLAlchemy 2.0 声明式 `Submission` 模型,含 enum status)
  - [ ] SubTask 1.2: 在 `backend/app/db/base.py` 导入 `Submission` 确保元数据注册
  - [ ] SubTask 1.3: 创建 Pydantic schemas `backend/app/schemas/__init__.py`、`backend/app/schemas/submission.py`(`SubmissionCreate`、`SubmissionOut` 列表精简版、`SubmissionDetail` 完整版)
  - [ ] SubTask 1.4: 生成 Alembic 迁移 `alembic revision --autogenerate -m "create submissions"`,检查迁移文件
  - [ ] SubTask 1.5: 执行 `alembic upgrade head` 创建表(需 PostgreSQL 运行)

## 阶段二:后端外部服务集成
- [ ] Task 2: 实现百度云 OCR 服务
  - [ ] SubTask 2.1: 创建 `backend/app/services/__init__.py` 与 `backend/app/services/ocr.py`
  - [ ] SubTask 2.2: 实现 `get_access_token(api_key, secret_key)`(POST 百度 OAuth 接口,缓存 token)
  - [ ] SubTask 2.3: 实现 `ocr_pdf(file_path: str) -> str`(读取 PDF → 调用百度 OCR 接口 → 拼接返回文本)
  - [ ] SubTask 2.4: 错误处理:API 错误、网络异常、空文本均抛出明确异常
- [ ] Task 3: 实现豆包 LLM 批改服务
  - [ ] SubTask 3.1: 创建 `backend/app/services/llm.py`
  - [ ] SubTask 3.2: 创建 `backend/app/core/prompt.py`(硬编码 rubric 与 prompt 模板,要求 JSON 输出 + CoT)
  - [ ] SubTask 3.3: 实现 `mark_submission(ocr_text: str) -> dict`(用 OpenAI SDK 兼容调用豆包,解析 JSON 响应)
  - [ ] SubTask 3.4: 错误处理:JSON 解析失败、API 错误、字段缺失均抛出明确异常,保留原始响应用于日志

## 阶段三:后端批改编排与接口
- [ ] Task 4: 实现批改任务编排
  - [ ] SubTask 4.1: 创建 `backend/app/services/marking.py`
  - [ ] SubTask 4.2: 实现 `run_marking_pipeline(submission_id: int)`(状态机:pending→ocr_processing→ocr_done→llm_processing→done,失败转 failed)
  - [ ] SubTask 4.3: 每个阶段更新数据库 status;失败时写 error_message
- [ ] Task 5: 实现上传与查询接口
  - [ ] SubTask 5.1: 创建 `backend/app/api/submissions.py`
  - [ ] SubTask 5.2: `POST /submissions`(接收 PDF、保存到 UPLOAD_DIR、创建记录、触发 BackgroundTask、返回 201)
  - [ ] SubTask 5.3: `GET /submissions`(列表,按 uploaded_at 降序,精简字段)
  - [ ] SubTask 5.4: `GET /submissions/{id}`(详情,完整字段,404 处理)
  - [ ] SubTask 5.5: 在 `main.py` 注册 submissions router(前缀 `/api`)

## 阶段四:前端业务页面
- [ ] Task 6: 实现前端 API hooks
  - [ ] SubTask 6.1: 创建 `frontend/src/api/submissions.ts`
  - [ ] SubTask 6.2: 实现 `useSubmissions()`(列表 Query,含轮询选项)
  - [ ] SubTask 6.3: 实现 `useSubmission(id)`(详情 Query,含轮询选项 enabled=非终态)
  - [ ] SubTask 6.4: 实现 `useUploadSubmission()`(mutation,POST multipart)
- [ ] Task 7: 改造上传页
  - [ ] SubTask 7.1: 修改 `frontend/src/pages/UploadPage.tsx`
  - [ ] SubTask 7.2: 使用 Ant Design `Upload.Dragger` + "开始批改"按钮
  - [ ] SubTask 7.3: 前端校验仅 PDF;上传中 loading;成功后 `navigate('/result/' + id)`
  - [ ] SubTask 7.4: 错误时 message.error 提示
- [ ] Task 8: 改造历史页
  - [ ] SubTask 8.1: 修改 `frontend/src/pages/HistoryPage.tsx`
  - [ ] SubTask 8.2: Ant Design Table 展示列表(文件名、状态 Tag、分数、上传时间、操作)
  - [ ] SubTask 8.3: 列表存在非终态时 `refetchInterval=3000`,全终态时停止
  - [ ] SubTask 8.4: "查看"按钮跳转 `/result/:id`
- [ ] Task 9: 改造结果页
  - [ ] SubTask 9.1: 修改 `frontend/src/pages/ResultPage.tsx`
  - [ ] SubTask 9.2: 非终态时 Spin + "正在批改中",`refetchInterval=2000`
  - [ ] SubTask 9.3: done 时展示:文件名、总分、反馈 Card、详细评分项列表、OCR 原文(折叠面板)
  - [ ] SubTask 9.4: failed 时 Ant Design Result(error)+ 返回上传页按钮

## 阶段五:端到端验证
- [ ] Task 10: 配置环境变量
  - [ ] SubTask 10.1: 在 `backend/.env` 填入真实的 `DOUBAO_API_KEY`、`BAIDU_OCR_API_KEY`、`BAIDU_OCR_SECRET_KEY`
  - [ ] SubTask 10.2: 确认 `DATABASE_URL` 指向可用的 PostgreSQL
- [ ] Task 11: 端到端测试
  - [ ] SubTask 11.1: 启动后端与前端
  - [ ] SubTask 11.2: 浏览器上传一个真实 PDF 作业
  - [ ] SubTask 11.3: 观察状态流转(pending→processing→done)
  - [ ] SubTask 11.4: 验证结果页展示分数、反馈、详细项、OCR 文本
  - [ ] SubTask 11.5: 验证历史页列表与跳转

# Task Dependencies
- Task 1 → Task 4(编排依赖模型)
- Task 2、Task 3 可并行(独立外部服务)
- Task 4 依赖 Task 1、2、3
- Task 5 依赖 Task 1、4
- Task 6 可与 Task 7-9 并行准备,但 7-9 依赖 6 的 hooks
- Task 7、8、9 可并行(三个页面独立)
- Task 11 依赖所有前置 Task 完成

# 实施约束
- 后端 OCR/LLM 调用必须使用 httpx 或 OpenAI SDK,不用 requests
- LLM 必须要求 JSON 输出并校验字段
- 任务编排中每一步必须更新数据库 status,失败必须写 error_message
- 前端轮询必须在终态时停止,避免无限请求
- prompt 中 rubric 硬编码即可,MVP 不做配置化
- 不实现用户认证、不做文件大小限制(除 PDF 类型)、不做并发数限制(MVP 简化)
