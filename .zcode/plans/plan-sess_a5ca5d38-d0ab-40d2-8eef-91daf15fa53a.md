## 目标

把 `question_id` 从自增整数主键改为**文件名 slug**（基于创建时 `original_filename` 的去扩展名部分生成的稳定标识符）。替换/重试 OCR 不改 id；重名上传拒绝；存量 4、6 两条记录自动迁移。

## 调研结论（已确认）

- `question_id` 不参与任何 hash/幂等键/文件路径构造。评分幂等靠 `(submission_id, request_id)`，文件存储按内容 sha256。它只作为：数据库外键、URL 路由键、SSE 事件匹配键。
- 排序不依赖 id 数值（`last_used_at desc, created_at desc`）；`func.count(Question.id)` 仅计数，字符串主键不受影响。
- 三个硬性约束必须处理：同名不唯一、`retry-ocr`/`replace` 会覆盖 `original_filename`、文件名含中文/空格需 URL 安全。

## 方案

### A. ID 生成规则（新增 `app/services/question_identity.py`）

- `slugify(stem)`：NFKC 归一化 → 去非法字符 → 保留中文/字母/数字/连字符/下划线 → 空格转 `-`，多个连字符合并，限长 100。
- `build_question_id(stem)`：`slugify(stem)`；若与已有题目 id 冲突，拒绝（由 API 层返回 409），不做自动加后缀。
- **id 稳定**：`retry-ocr`、`replace` 只更新 `file_path`/`original_filename`/`ocr_text`，**不动 `id`**。id 恒为创建时文件名 slug。
- 创建时在 `db.add` 前就确定 id（不再依赖 flush 后自增回填），因此 `new_question_ocr_job(question.id)` 的调用时机不变。

### B. 数据库模型（4 个文件）

| 文件 | 改动 |
|---|---|
| `app/models/question.py` | `id: Mapped[str] = mapped_column(String(100), primary_key=True)` |
| `app/models/submission.py` | `question_id: Mapped[str]` + FK `questions.id`（保持 `ondelete="RESTRICT"`, index） |
| `app/models/background_job.py` | `question_id: Mapped[str | None]` + FK（保持 `unique=True`, CASCADE） |
| `app/models/mcp_workflow_handle.py` | `question_id: Mapped[str | None]` + FK（保持 CASCADE, index） |

### C. Alembic 迁移（新增一个，`question_id_string_pk.py`）

1. `questions` 表新增临时列 `id_new`(String(100))，按 `created_at` 顺序为每条记录生成 slug（冲突加 `-2`/`-3` 后缀，因存量数据需保唯一；后续新创建走"拒绝"逻辑）。
2. 依次给 3 张子表加临时列 `question_id_new`(String)，用 JOIN 从 `questions` 回填旧 id → 新 id 映射。
3. 删旧 FK → 删旧 PK 约束 → 删旧 `id` 列 → 重命名 `id_new` → `id`，并重建 PK。子表同理（删旧 FK、换列类型、回填、重建 FK）。
4. 重建 `background_jobs` 的 unique 约束、`submissions`/`mcp_workflow_handles` 的索引。
5. 反向迁移还原。

### D. 后端 API / Schemas / 服务层

- `app/schemas/question.py`：`QuestionOut.id: str`、`GradingPromptOut.question_id: str`、`QuestionReplacementResponse.question_id: str`。
- `app/schemas/mcp.py`：`McpQuestionCandidate.id: str`、`McpPreflightResponse.question_id: str | None`、`McpPackageResponse.question_id: str | None`、`McpSaveRubricResponse.question_id: str`。
- `app/schemas/admin.py`：`DeadJobOut.question_id: str | None`。
- `app/api/questions.py`：
  - 所有 `{question_id}` 路径参数 `int → str`（get/detail/grading-prompt/pdf/rename/config-profile/retry-ocr/replace/delete）。
  - `create_question`：构造 id = `build_question_id(stem)`；先查重，冲突返回 409「同名题目已存在，请改名后上传」；写库前即确定 id。
  - 路径参数 slug 反向查找：`db.get(Question, question_id)` 直接按字符串 id 取。
- `app/api/submissions.py`：`create_submission` 的 Form 字段 `question_id: int` → `str`；`db.get(Question, question_id)` 不变。
- `app/api/mcp.py`：`save_mcp_question_rubric` 路径参数 `question_id: int → str`。
- `app/services/events.py`：`question_event_stream(question_id)` 与 `_event_loop` 的 `match_value: int` → `str`（SSE 匹配逻辑本身不变）。
- `app/mcp/server.py`：`save_ai_marking_question_rubric(question_id: str)`；`submit_prepared_ai_marking_submission` 里 `str(plan["question_id"])` 不变（已是字符串）；URL 拼接保持模板字符串（FastAPI 会自动做路径编码）。
- worker/queue/question_ocr/question_replace：函数签名 `question_id: int → str`（内部 `db.get(Question, ...)` 逻辑不变）。

### E. 前端（5 个文件 + 4 个测试）

- `src/api/questions.ts`：`Question.id: number → string`、`GradingPrompt.question_id: number → string`、`useGradingPrompt(questionId: string | null)`、各 mutation 变量 `{ id: string }`、`useReplaceQuestion` 返回 `question_id: string`。
- `src/lib/gradingPrompt.ts`：`GradingPromptInput.questionId: number → string`，去掉 `String()` 包装。
- `src/components/questions/QuestionCard.tsx`：`onRetryUpload: (id: string, file)`；`/api/questions/${question.id}/pdf` 链接自动兼容字符串。
- 测试：`gradingPrompt.test.ts`、`QuestionPromptDialog.test.tsx`、`QuestionsPage.test.tsx`、`UploadQuestionsPage.test.tsx` 的硬编码数字 id 改为字符串（如 `'DTS208TC_CW1_Paper'` 或 `'7'`）。
- URL/queryKey/React key/缓存失效逻辑均天然兼容字符串，无需改。

### F. 契约文档

- `.agents/skills/ai-marking-grader/SKILL.md`：更新 `question_id` 描述为"文件名 slug 字符串"。
- `docs/data/schema.md`、`docs/architecture/mcp-grading.md`、`PROJECT.md`：同步更新 id 类型说明。

### G. 后端测试

- 更新硬编码数字：`test_mcp_server.py`（:25/:52/:101/:188/:195）、`test_submissions_api.py`（:44 `question_id: "1"` → 字符串文件名）。
- 动态取 `question.id` 的断言（test_questions_api/test_mcp_api/test_submissions_api/test_marking/test_queue/test_queue_postgres/test_question_replace）类型随主键变字符串，逻辑不变，跑通即可。
- 新增测试：`build_question_id` 的 slug 规则、重名拒绝 409、create 返回字符串 id。

## 风险与注意

1. **存量迁移是最大风险点**：3 张子表 FK 重建 + 数据回填必须在一个事务里，先备份数据库再跑。
2. **MCP 契约变更**：`save_ai_marking_question_rubric` 工具签名从 int 改 str，已部署的编程助手客户端需同步（本项目内 skill 会一起改，属内部一致）。
3. **URL 中的中文文件名**：FastAPI 路径参数对 percent-encoded 字符串自动解码，前端 axios 自动编码，无需手工处理。
4. **同名拒绝是新行为**：之前允许同名共存，现在第二个同名上传会 409。若未来需要同名，可在 id 后追加用户自定义编号。
5. 提交前我会先跑 `pytest backend/tests/` 确认迁移与回归，前端跑 `npm test` 或对应测试文件。

## 执行顺序

1. 写 `question_identity.py` + slug 单测
2. 改 4 个模型 + 写 Alembic 迁移（先备份 DB）
3. 改后端 API/schemas/services/mcp + 后端测试
4. 改前端类型 + 测试
5. 更新契约文档
6. 跑迁移（迁移脚本本身 + 实际 DB 验证 4、6 → slug）、跑全量测试
