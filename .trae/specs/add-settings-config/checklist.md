# 验收检查清单

## 数据模型
- [x] `SystemConfig` 模型包含:id、key(unique)、value(Text)、description、updated_at
- [x] `key` 字段有唯一约束
- [x] Alembic 迁移文件已生成且可执行 `alembic upgrade head` 创建 `system_config` 表
- [x] Pydantic schemas 区分 `ConfigUpdate`(入参,字段可选,extra=forbid)与 `ConfigOut`(出参,固定 6 字段)

## 配置服务
- [x] `app/services/config.py` 实现 `get_config_dict(db)` 与 `upsert_config(db, updates)`
- [x] `CONFIG_KEYS` 常量声明 6 个 key 及其 description
- [x] `get_config_dict` 带 5 秒内存缓存
- [x] `upsert_config` 校验未知 key(抛 ConfigError)
- [x] `upsert_config` 完成后调用 `invalidate_config_cache()`

## 配置 API
- [x] `GET /api/config` 返回 `ConfigOut`(6 字段,缺失填空字符串)
- [x] `PUT /api/config` 接收 `ConfigUpdate` 子集
- [x] 未知 key 通过 Pydantic `extra=forbid` 返回 422
- [x] PUT 完成后返回更新后的完整 `ConfigOut`
- [x] `main.py` 已注册 config router(`/api` 前缀)
- [x] OpenAPI 文档验证:GET /api/config、PUT /api/config、GET /api/health、GET /api/submissions、POST /api/submissions、GET /api/submissions/{id} 全部注册

## OCR 服务改造
- [x] `app/services/ocr.py` 不再 `from app.core.config import settings`
- [x] `get_access_token(api_key, secret_key)` 接收参数
- [x] `ocr_pdf(file_path, api_key, secret_key)` 接收参数
- [x] 配置缺失抛 `OCRError("百度 OCR API Key 未配置,请在设置页填写")`
- [x] access_token 缓存按 (api_key, secret_key) 维度

## LLM 服务改造
- [x] `app/services/llm.py` 不再 `from app.core.config import settings`
- [x] `_get_client(api_key, base_url)` 接收参数
- [x] `mark_submission(ocr_text, config: dict)` 接收配置字典
- [x] `doubao_api_key` 缺失抛 `LLMError("豆包 API Key 未配置,请在设置页填写")`
- [x] `doubao_base_url` / `doubao_model` 为空时使用代码默认值(DEFAULT_DOUBAO_BASE_URL / DEFAULT_DOUBAO_MODEL)

## Prompt 与 Marking 改造
- [x] `build_user_prompt(ocr_text, rubric=None)` 支持外部传入 rubric
- [x] rubric 为空/None 时使用默认 `RUBRIC`
- [x] `run_marking_pipeline` 开始处调用 `get_config_dict(db)` 读取一次配置
- [x] 调用 `ocr_pdf` 时传入百度 Key/Secret 参数
- [x] 调用 `mark_submission` 时传入 config 字典参数
- [x] rubric 由 marking 从 config 取出后,通过 `mark_submission` 内部传给 `build_user_prompt`

## Settings 清理
- [x] `app/core/config.py` 移除 `DOUBAO_API_KEY`、`DOUBAO_BASE_URL`、`DOUBAO_MODEL`、`BAIDU_OCR_API_KEY`、`BAIDU_OCR_SECRET_KEY`
- [x] 保留 `DATABASE_URL`、`CORS_ORIGINS`、`UPLOAD_DIR`
- [x] 代码中保留 `DEFAULT_DOUBAO_BASE_URL` 与 `DEFAULT_DOUBAO_MODEL` 常量供回退
- [x] `.env` 与 `.env.example` 同步清理

## 前端 config API
- [x] `frontend/src/api/config.ts` 定义 `ConfigOut` 与 `ConfigUpdate` 类型
- [x] `useConfig()` Query key 为 `['config']`
- [x] `useUpdateConfig()` Mutation 成功后 setQueryData + invalidateQueries

## 前端设置页
- [x] `SettingsPage.tsx` 渲染 6 字段表单
- [x] API Key / Secret Key 使用 `Input.Password`
- [x] 使用 3 个 Card 分组(豆包 LLM、百度 OCR、评分标准)
- [x] 加载时通过 `useConfig` 回填表单
- [x] "保存配置"按钮调用 PUT,loading + 成功/失败 message
- [x] "重置 Rubric 为默认"按钮

## 前端导航与路由
- [x] `MainLayout.tsx` 新增"设置"菜单项(`SettingOutlined`)
- [x] `routeMap` 含 `settings: '/settings'`
- [x] `selectedKey` 逻辑支持 `/settings` 高亮
- [x] `router/index.tsx` 新增 `{ path: 'settings', element: <SettingsPage /> }`

## 代码质量
- [x] TypeScript 编译无错误(`tsc --noEmit`)
- [x] oxlint 通过(0 warnings, 0 errors)
- [x] ruff check 通过
- [x] black 格式化通过

## API 联调验证(已通过)
- [x] `GET /api/config` 返回 6 字段空配置
- [x] `PUT /api/config` upsert 成功并持久化(再次 GET 确认)
- [x] 未知字段返回 422
- [x] 空字符串清空配置成功
- [x] 上传 PDF 后状态流转 pending → failed,error_message 为"百度 OCR API Key 未配置,请在设置页填写"

## 端到端验证(待用户)
- [ ] 在设置页填入真实豆包 API Key 与百度 OCR Key/Secret
- [ ] 上传真实 PDF 后状态流转完整(pending → ocr_processing → ocr_done → llm_processing → done)
- [ ] 结果页正确展示分数、反馈、详细项、OCR 文本
- [ ] 历史页正确展示列表与跳转
- [ ] 清空豆包 Key 后上传,验证 error_message 提示"豆包 API Key 未配置,请在设置页填写"
- [ ] 修改 rubric 后重新上传,验证 LLM 按新 rubric 批改
- [ ] 重启后端后配置仍存在(数据库持久化)
