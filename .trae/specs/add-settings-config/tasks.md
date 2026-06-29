# Tasks

## 阶段一:后端数据模型与配置服务
- [x] Task 1: 创建 SystemConfig 模型与迁移
  - [x] SubTask 1.1: 创建 `backend/app/models/system_config.py`(`SystemConfig` 模型:id、key(unique)、value(Text)、description、updated_at)
  - [x] SubTask 1.2: 在 `backend/app/models/__init__.py` 导出 `SystemConfig`
  - [x] SubTask 1.3: 在 `backend/app/db/base.py` 导入 `SystemConfig` 确保元数据注册
  - [x] SubTask 1.4: 创建 Pydantic schemas `backend/app/schemas/system_config.py`(`ConfigUpdate` 入参、`ConfigOut` 出参,固定 6 字段)
  - [x] SubTask 1.5: 生成 Alembic 迁移 `alembic revision --autogenerate -m "create system_config"`,检查迁移文件
  - [x] SubTask 1.6: 执行 `alembic upgrade head` 创建表(需 PostgreSQL 运行)
- [x] Task 2: 实现配置读取服务
  - [x] SubTask 2.1: 创建 `backend/app/services/config.py`
  - [x] SubTask 2.2: 定义 `CONFIG_KEYS` 常量(6 个 key 及其 description)
  - [x] SubTask 2.3: 实现 `get_config_dict(db) -> dict[str, str]`(查表返回所有 key-value,带 5 秒内存缓存)
  - [x] SubTask 2.4: 实现 `upsert_config(db, updates: dict) -> dict[str, str]`(校验 key、upsert、失效缓存、返回新配置)
  - [x] SubTask 2.5: 实现 `invalidate_config_cache()` 辅助函数

## 阶段二:后端 API 与服务改造
- [x] Task 3: 实现配置 API 接口
  - [x] SubTask 3.1: 创建 `backend/app/api/config.py`
  - [x] SubTask 3.2: `GET /config`(读取并返回 `ConfigOut` 结构,缺失字段填空字符串)
  - [x] SubTask 3.3: `PUT /config`(接收 `ConfigUpdate` 子集,upsert,未知 key 返回 422 通过 Pydantic `extra=forbid`,返回更新后完整配置)
  - [x] SubTask 3.4: 在 `main.py` 注册 config router(前缀 `/api`,tags `["config"]`)
- [x] Task 4: 改造 OCR 服务从 DB 读配置
  - [x] SubTask 4.1: 修改 `backend/app/services/ocr.py`
  - [x] SubTask 4.2: `get_access_token(api_key, secret_key)` 改为接收参数,移除 `from app.core.config import settings`
  - [x] SubTask 4.3: `ocr_pdf(file_path, api_key, secret_key)` 改为接收参数
  - [x] SubTask 4.4: 配置缺失抛出 `OCRError("百度 OCR API Key 未配置,请在设置页填写")`
- [x] Task 5: 改造 LLM 服务从 DB 读配置
  - [x] SubTask 5.1: 修改 `backend/app/services/llm.py`
  - [x] SubTask 5.2: `_get_client(api_key, base_url)` 改为接收参数,移除 `from app.core.config import settings`
  - [x] SubTask 5.3: `mark_submission(ocr_text, config: dict)` 改为接收配置字典,从中取 `doubao_api_key` / `doubao_base_url` / `doubao_model`(后两者为空时使用代码默认)
  - [x] SubTask 5.4: 配置缺失抛出 `LLMError("豆包 API Key 未配置,请在设置页填写")`
- [x] Task 6: 改造 prompt 与 marking 编排
  - [x] SubTask 6.1: 修改 `backend/app/core/prompt.py`,`build_user_prompt(ocr_text, rubric=None)`,rubric 为空/None 时回退默认
  - [x] SubTask 6.2: 修改 `backend/app/services/marking.py`
  - [x] SubTask 6.3: 在 `run_marking_pipeline` 开始处调用 `get_config_dict(db)` 读取配置一次
  - [x] SubTask 6.4: 调用 `ocr_pdf(sub.file_path, baidu_api_key, baidu_secret_key)` 传参
  - [x] SubTask 6.5: 调用 `mark_submission(ocr_text, config_dict)` 传参(rubric 由 `build_user_prompt` 内部从 config_dict 取)
- [x] Task 7: 清理 settings 配置
  - [x] SubTask 7.1: 修改 `backend/app/core/config.py`,移除 `DOUBAO_API_KEY`、`DOUBAO_BASE_URL`、`DOUBAO_MODEL`、`BAIDU_OCR_API_KEY`、`BAIDU_OCR_SECRET_KEY`
  - [x] SubTask 7.2: 保留 `DATABASE_URL`、`CORS_ORIGINS`、`UPLOAD_DIR`
  - [x] SubTask 7.3: 保留代码默认值(`DEFAULT_DOUBAO_BASE_URL`、`DEFAULT_DOUBAO_MODEL`)到 `llm.py`,供回退使用

## 阶段三:前端设置页与导航
- [x] Task 8: 实现前端 config API hook
  - [x] SubTask 8.1: 创建 `frontend/src/api/config.ts`
  - [x] SubTask 8.2: 定义类型 `ConfigOut`(6 字段 + updated_at)、`ConfigUpdate`(6 字段可选)
  - [x] SubTask 8.3: 实现 `useConfig()`(GET Query,key `['config']`)
  - [x] SubTask 8.4: 实现 `useUpdateConfig()`(PUT Mutation,成功后 setQueryData + invalidate `['config']`)
- [x] Task 9: 实现设置页
  - [x] SubTask 9.1: 创建 `frontend/src/pages/SettingsPage.tsx`
  - [x] SubTask 9.2: 用 Ant Design `Form` 渲染 6 字段,API Key/Secret 使用 `Input.Password`
  - [x] SubTask 9.3: 使用 3 个 `Card` 分组:豆包 LLM 配置、百度 OCR 配置、评分标准
  - [x] SubTask 9.4: 加载时 `useConfig` 取数据并通过 `Form.setFieldsValue` 回填
  - [x] SubTask 9.5: "保存配置"按钮调用 `useUpdateConfig`,loading + 成功/失败 message
  - [x] SubTask 9.6: "重置 Rubric 为默认"按钮,清空 rubric 字段
- [x] Task 10: 注册路由与导航
  - [x] SubTask 10.1: 修改 `frontend/src/router/index.tsx`,新增 `{ path: 'settings', element: <SettingsPage /> }`
  - [x] SubTask 10.2: 修改 `frontend/src/layouts/MainLayout.tsx`,新增"设置"菜单项(`SettingOutlined`)
  - [x] SubTask 10.3: 更新 `routeMap` 与 `selectedKey` 逻辑,支持 `/settings` 路径高亮

## 阶段四:端到端验证
- [x] Task 11: API 联调验证(已通过)
  - [x] SubTask 11.1: 启动后端
  - [x] SubTask 11.2: `GET /api/config` 返回 6 字段空配置
  - [x] SubTask 11.3: `PUT /api/config` upsert 成功并持久化(再次 GET 确认)
  - [x] SubTask 11.4: 未知字段返回 422(Pydantic extra=forbid)
  - [x] SubTask 11.5: 空字符串清空配置成功
  - [x] SubTask 11.6: 上传 PDF 后状态流转 pending → failed,error_message 提示"百度 OCR API Key 未配置,请在设置页填写"
- [ ] Task 12: 真实 API 端到端测试(交付用户)
  - [ ] SubTask 12.1: 启动后端与前端
  - [ ] SubTask 12.2: 在设置页填入真实的豆包 API Key、百度 OCR Key/Secret
  - [ ] SubTask 12.3: 上传一个真实 PDF 作业
  - [ ] SubTask 12.4: 验证状态流转 pending → ocr_processing → ocr_done → llm_processing → done
  - [ ] SubTask 12.5: 验证结果页正确展示分数、反馈、详细项、OCR 文本
  - [ ] SubTask 12.6: 修改 rubric 后重新上传,验证 LLM 按新 rubric 批改

# Task Dependencies
- Task 1 → Task 2(配置服务依赖模型)
- Task 2 → Task 3(API 依赖配置服务)
- Task 2 → Task 4、Task 5、Task 6(OCR/LLM/marking 依赖配置服务签名)
- Task 4、Task 5、Task 6 互相独立,已并行实施
- Task 7 在 Task 4、Task 5 完成后执行
- Task 8 → Task 9(设置页依赖 hooks)
- Task 9、Task 10 已并行实施
- Task 11 依赖 Task 1-10 完成,已通过 API 联调
- Task 12 由用户在浏览器端完成真实 API 测试

# 实施约束
- 后端 OCR/LLM 服务签名变更后,`marking.py` 已同步更新调用方式
- 配置 API 不做鉴权(MVP 内部工具)
- 敏感字段在 GET 接口返回真实值,前端通过 `Input.Password` 隐藏
- `system_config` 表使用 key-value 设计,便于后续扩展
- 缓存 5 秒 TTL,PUT 后主动失效
- 未知 key 通过 Pydantic `extra=forbid` 返回 422(FastAPI 标准)
- 不实现用户认证、不做配置变更历史(MVP 简化)
- 配置项 key 列表在 `services/config.py` 的 `CONFIG_KEYS` 常量维护
