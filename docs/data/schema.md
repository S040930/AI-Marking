# 数据模型

## 表与职责

| 表 | 核心数据 | 生命周期 |
|---|---|---|
| `questions` | 题目 PDF、OCR 文本、服务端校验通过的题目提取 rubric 快照（`extracted_rubric*`）与新版替换状态 | 可被多份作业复用；新版成功后切换 |
| `submissions` | 学生 PDF 路径、作业 OCR、MCP 评分建议与人工审核结果 | 必须关联一个题目；终态记录可单独删除 |
| `submission_code_files` | 编程助手提交的多语言小题源码与 SHA-256（不保存运行产物） | 随 submission 级联删除 |
| `submission_code_input_files` | 历史数据：旧代码运行输入文件，仅只读保留 | 随 submission 级联删除 |
| `background_jobs` | 待执行、运行中或死信的题目 OCR/题目替换/作业 OCR 任务 | 成功后删除；随业务实体级联删除 |
| `system_config` | OCR、结构化 `rubric_definition` 与 `review_enabled` 配置 | 独立于业务记录长期保留 |
| `config_profiles` | 独立配置项目及默认标记 | 被题目引用或为默认时不可删除 |
| `mcp_workflow_handles` | MCP 评分包句柄（续页、revision/context 绑定）与题目 rubric 提取句柄 | 保存或提取完成后删除，TTL 过期清理 |
| `mcp_assessment_receipts` | 外部编程助手成功保存的请求哈希与响应 | 用于跨 revision、重启的精确幂等重放 |
| `alembic_version` | 当前数据库迁移版本 | 由 Alembic 管理 |

`submissions` 的 MCP 审计字段：`grading_mode`（收敛为 MCP-only 后固定为 `external_agent`）、`grading_revision`（乐观锁整数，每次成功保存建议递增）和 `graded_at`（最近一次建议保存时间）。评分来源记录在 `assessment_suggestion.mcp_metadata.client`（如 `codex`、`claude-code`、`opencode`），界面据此显示具体客户端名称，不保存未经验证的模型名称。后端不再调用任何 LLM，也不存在 Agent/Critic 执行字段。

`questions.extracted_rubric*` 只有在 rubric item 的 OCR 原文引用、分值和总分通过后端确定性校验且 OCR SHA-256 匹配时才构成可信 `question_extracted` 快照；快照由编程助手经 `save_ai_marking_question_rubric` 提取、服务端校验后写入，历史旧文本没有来源标记时不参与 MCP rubric 选择。

`mcp_workflow_handles` 同时承载两类句柄：评分包句柄（绑定 submission、revision 与 context hash，用于 `save_ai_marking_assessment`）与题目 rubric 提取句柄（绑定 `question_id` 与 `ocr_hash`，用于 `save_ai_marking_question_rubric`）；保存成功后句柄即失效删除，避免重复提取覆盖快照。

编程助手代码联动字段：新提交只写入 `submission_code_files` 中的题号、入口标记、规范化源文本和 SHA-256。后端不编译或执行代码，不保存运行输入、日志、Notebook 新输出或代码产物；代码运行由当前编程助手任务在临时目录中完成，结果只存在于对话。`submission_code_input_files`、执行状态、产物和 `visual_reviews` 仅作为历史数据只读保留，新评分上下文不会读取它们。

## 关系

```text
questions 1 ────── N submissions 1 ────── N submission_code_files
    │                    │
    └──── 0..1 background_jobs 0..1 ─────┘
```

- `submissions.question_id → questions.id` 使用 `ON DELETE RESTRICT`，题目危险操作由应用层锁定记录、检查处理状态并在事务内显式删除关联作业。
- `submission_code_files.submission_id → submissions.id` 使用 `ON DELETE CASCADE`。
- `background_jobs` 每行只允许关联一个题目或一份作业；两个外键均使用 `ON DELETE CASCADE`。
- `conversations` 表已随后端 Agent 链路删除，不存在教师—AI 对话数据。

## 后台任务状态

```text
queued → running → 成功后删除
            ├─ 业务失败(BusinessError) → 直接标终态 + 删除任务(不重试)
            ├─ 系统失败且未耗尽 → queued（指数退避）
            ├─ 系统失败耗尽 → dead（死信,需运维介入）
            └─ worker 崩溃 → 租约到期后重新领取
```

- worker 使用领取令牌完成和续租，旧 worker 不能完成已被重新领取的任务。
- API 与任务记录在同一事务内创建，不会出现业务记录成功但任务丢失。
- 任务类型包括 `question_ocr`、`question_replace` 和 `submission_ocr`。
- **失败类型判定**：
  - 业务失败（配置错误 / 文件不可识别 / OCR 返回为空或结构异常 / PaddleOCR 地址配置错误）：`ocr_pdf` 抛出 `BusinessError`，worker 立即标记目标终态并删除任务行，**不重试**。
  - 系统失败（网络瞬时抖动耗尽 / 数据库中断 / 进程崩溃等其余异常）：worker 保留队列退避重试，达到 `TASK_MAX_ATTEMPTS` 后转死信。
  - OCR 内部对超时 / 限流 / 5xx 进行最多 3 次短暂重试；这 3 次网络重试耗尽仍属系统失败（可重试），而配置类错误属业务失败（不重试）。
- 教师重新上传 PDF / 题目后才创建新任务，系统不会自动重跑原文件。

## 删除 / 替换 / 重试的数据生命周期

### 单条作业删除（终态）

`DELETE /api/submissions` 仅接受可删除终态（`awaiting_mcp` / `ready_for_review` / `reviewed` / `failed`）记录，存在后台处理中记录时整批原子拒绝。数据库先删记录（级联删 `submission_code_files`），提交成功后再清理学生 PDF；**共享题目 PDF 始终保留**。文件删除失败仅记录 warning，数据库是删除结果的权威来源。

### 题目级联删除

`DELETE /api/questions` 要求输入完整题目名称确认，且题目下存在处理中作业时拒绝。删除题目时事务内显式删除其关联作业与源码文件（CASCADE），并清理题目 PDF 与学生 PDF。

### 题目新版替换

新版 PDF 先进入 `question_replace` 队列，处理期间题目冻结（`replacement_status=pending/processing`）。OCR 成功后在事务内删除旧批改记录并原子切换 `file_path` / `ocr_text` / `original_filename`；因新 OCR 文本哈希变化，旧题目提取 rubric 快照一并失效（`extracted_rubric*` 置空，下次评分重新提取）。OCR 业务失败（`BusinessError`）时旧题目与旧 OCR 保持不变，仅 `replacement_status=failed`，并清理暂存 PDF，不污染原题目。

### 作业重试

失败作业通过 `POST /api/submissions/{id}/retry` 在原记录上重新入队，可选替换学生 PDF。重试会清空旧 OCR、评分建议、分数与审核数据，并重置 MCP 评分句柄；若原 PDF 已被定期清理则必须重新上传。

## 状态

题目：

```text
pending → ocr_processing → ready
                         └→ failed
```

题目新版：

```text
null → pending → processing → 成功后回到 null
                           └→ failed（旧题目保持 ready）
```

- 暂存新版保存在 `replacement_file_path`，处理期间题目被冻结。
- 新版成功后事务内删除旧批改并切换；失败时旧 PDF、旧 OCR 和历史记录保持不变。

作业（MCP-only）：

```text
pending → ocr_processing → ocr_done → awaiting_mcp
  → ready_for_review → reviewed

任一处理阶段可进入 failed
```

- OCR 完成后进入 `awaiting_mcp`，由编程助手通过 `open_ai_marking_assignment` 打开评分包并保存建议；保存后进入 `ready_for_review`，教师在网页表单式人工复核并确认后写入 `reviewed`。
- `submissions.grading_mode` 固定为 `external_agent`；`grading_revision` 从 0 开始，每次成功保存外部助手建议递增；`graded_at` 为最近一次建议保存时间。`awaiting_mcp` 不会自动超时，编程助手会话退出后仍可通过 `list_pending_ai_marking_assignments` 重新发现并读取上下文。

## 文件生命周期

- 题目 PDF 路径存储于 `questions.file_path`，定期清理任务会保护仍被题目表引用的文件。
- 学生 PDF 路径存储于 `submissions.file_path`，删除作业后在数据库提交成功后清理。
- 定期清理会删除超过保留期且不受保护的 PDF；数据库中的 OCR 文本和评分记录继续保留。
- 文件删除失败只记录 warning，数据库是删除结果的权威来源。

## 数据敏感性

- `system_config` 当前包含外部服务密钥（PaddleOCR token），属于敏感数据，不应出现在日志、API 明文展示或版本控制中。
- 学生作业、OCR 文本与评分可能包含个人信息，应限制数据库、备份和上传目录的访问权限。
