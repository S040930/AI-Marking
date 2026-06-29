# 验收检查清单

## 数据模型
- [ ] `Submission` 模型包含:id、original_filename、file_path、status、ocr_text、score、feedback、details、error_message、uploaded_at、completed_at
- [ ] `status` 使用 Enum(pending、ocr_processing、ocr_done、llm_processing、done、failed)
- [ ] `details` 字段为 JSON 类型
- [ ] Alembic 迁移文件已生成且可执行 `alembic upgrade head` 创建表
- [ ] Pydantic schemas 区分列表精简版与详情完整版

## 上传接口
- [ ] `POST /api/submissions` 接收 multipart/form-data 的 `file` 字段
- [ ] 仅接受 `application/pdf` MIME,非 PDF 返回 400
- [ ] 文件保存到 `UPLOAD_DIR`(UUID 命名,保留扩展名)
- [ ] 创建 Submission 记录(status=pending)
- [ ] 触发 BackgroundTask 执行批改流程
- [ ] 返回 201 + submission JSON(含 id)

## OCR 服务
- [ ] `app/services/ocr.py` 实现 `get_access_token` 与 `ocr_pdf`
- [ ] access_token 通过 `BAIDU_OCR_API_KEY` + `BAIDU_OCR_SECRET_KEY` 获取
- [ ] 调用百度 OCR API 成功返回纯文本
- [ ] API 错误、网络异常、空文本均有明确异常抛出

## LLM 服务
- [ ] `app/services/llm.py` 使用 OpenAI SDK 兼容方式调用豆包
- [ ] 使用 `DOUBAO_API_KEY`、`DOUBAO_BASE_URL`、`DOUBAO_MODEL` 配置
- [ ] `app/core/prompt.py` 包含 rubric 与要求 JSON 输出的 prompt 模板
- [ ] 返回 JSON 包含 score、feedback、details(数组,每项 criterion/score/comment)
- [ ] JSON 解析失败时抛出异常并保留原始响应用于日志

## 任务编排
- [ ] `app/services/marking.py` 实现 `run_marking_pipeline(submission_id)`
- [ ] 状态流转:pending → ocr_processing → ocr_done → llm_processing → done
- [ ] 每阶段更新数据库 status
- [ ] 任何阶段失败立即终止,status=failed,error_message 写入

## 查询接口
- [ ] `GET /api/submissions` 返回列表(按 uploaded_at 降序,精简字段)
- [ ] `GET /api/submissions/{id}` 返回完整字段
- [ ] 不存在的 id 返回 404

## 前端上传页
- [ ] 使用 Ant Design `Upload.Dragger` 选择文件
- [ ] 前端校验仅 PDF,非 PDF 提示且不发起请求
- [ ] 上传中显示 loading
- [ ] 成功后跳转 `/result/:id`
- [ ] 失败时 `message.error` 提示

## 前端历史页
- [ ] Ant Design Table 展示列表(文件名、状态、分数、上传时间、操作)
- [ ] 状态用 Tag 着色(处理中=蓝、done=绿、failed=红)
- [ ] 存在非终态记录时每 3 秒轮询
- [ ] 全部终态后停止轮询
- [ ] "查看"按钮跳转 `/result/:id`

## 前端结果页
- [ ] 非终态时显示 Spin + "正在批改中"
- [ ] 非终态时每 2 秒轮询详情
- [ ] done 时展示:文件名、总分、反馈 Card、详细评分项、OCR 原文(折叠)
- [ ] failed 时 Ant Design Result(error)+ 返回上传页按钮
- [ ] 进入终态后停止轮询

## 端到端验证
- [ ] `backend/.env` 填入真实 API Key
- [ ] PostgreSQL 可用且表已创建
- [ ] 上传真实 PDF 后状态流转完整(pending→processing→done)
- [ ] 结果页正确展示分数、反馈、详细项、OCR 文本
- [ ] 历史页正确展示列表与跳转
- [ ] 失败场景(如故意填错 Key)能正确展示 error_message
