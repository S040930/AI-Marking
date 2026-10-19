# 数据模型

## 表与职责

- `questions.id` 是主键，为**上传文件名去扩展名后的 slug 字符串**（如 `DTS208TC_CW1_Paper`），不再使用自增整数；id 在创建时由文件名生成且保持稳定，替换/重试 OCR 不改 id，同名文件重复上传返回 409 拒绝。

| 表 | 核心数据 | 生命周期 |
|---|---|---|
| `questions` | 题目 PDF、`file_sha256`、OCR 文本、服务端校验通过的题目提取 rubric 快照（`extracted_rubric*`）与新版替换状态 | 可被多份作业复用；新版成功后切换 |
| `submissions` | 学生 PDF 路径、`file_sha256`、作业 OCR、批改 agent 评分建议与人工审核结果 | 必须关联一个题目；终态记录可单独删除 |
| `submission_code_files` | 多语言小题源码与 SHA-256（不保存运行产物） | 随 submission 级联删除 |
| `submission_code_input_files` | 历史数据：旧代码运行输入文件，仅只读保留 | 随 submission 级联删除 |
| `background_jobs` | 待执行、运行中或死信的题目 OCR/题目替换/作业 OCR 任务 | 成功后删除；随业务实体级联删除 |
| `system_config` | OCR、结构化 `rubric_definition` 与模型通道配置（含 API key，只写不读） | 独立于业务记录长期保留 |
| `grading_runs` | 批改运行（状态机 8 态、provider/model/effort、`agent_state`（检查点对话项与句柄）、`applied_config`/`pending_config`、`usage`、租约与重试计数） | 终态永久保留；非终态由租约与恢复扫描管理 |
| `grading_run_events` | run 事件转录（seq 单调递增，SSE 回放与 NOTIFY 推送） | 随 run 保留 |
| `grading_handles` | 评分包句柄（续页、revision/context 绑定）与题目 rubric 提取句柄 | 保存或提取完成后删除，TTL 24h 过期清理 |
| `grading_assessment_receipts` | 批改 agent 成功保存的请求哈希与响应 | 用于跨 revision、重启的精确幂等重放 |
| `alembic_version` | 当前数据库迁移版本 | 由 Alembic 管理 |

`submissions` 的批改审计字段：`grading_mode`（固定为 `agent`）、`grading_revision`（乐观锁整数，每次成功保存建议递增）和 `graded_at`（最近一次建议保存时间）。评分来源记录在 `assessment_suggestion.agent_metadata`（含 `source`、rubric 来源与快照、context hash、revision、请求哈希与生成时间；v12 及更早历史数据为 `mcp_metadata` 键，读侧兼容桥接）。v12 起建议明细由服务端按权威 rubric 补全 `criterion`/`max_score` 并计算总分，旧数据中的 `quality_checks.client_self_check`/`second_pass_valid` 与 `review_required` 字段仅为历史残留，不再产生。

`submissions.assessment_review` 存储独立复核任务对当前建议的结论（总体 `verdict` agree/partial/disagree、`summary`、逐项 `items` 含服务端补全的 criterion/max_score 与可选 `suggested_score`、`reviewed_revision`、复核客户端与时间）。复核结论由 `save_assessment_review` 函数写入（v1 仅保留函数与数据字段，无前端发起入口）：不修改建议本身、不改变状态；`reviewed_revision` 与当前 `grading_revision` 不一致时界面标记为过期，重试批改时随建议一起清空。

`questions.extracted_rubric*` 只有在 rubric item 的 OCR 原文引用、分值和总分通过后端确定性校验且 OCR SHA-256 匹配时才构成可信 `question_extracted` 快照；快照由批改 agent 经 `save_question_rubric` 工具提取、服务端校验后写入，历史旧文本没有来源标记时不参与 rubric 选择。

`grading_handles` 同时承载两类句柄：评分包句柄（绑定 submission、revision 与 context hash，用于 `save_assessment`）与题目 rubric 提取句柄（绑定 `question_id` 与 `ocr_hash`，用于 `save_question_rubric`）；保存成功后句柄即失效删除，避免重复提取覆盖快照。TTL 为 24h（含代码检查点可跨夜驻留）。

代码联动字段：新提交只写入 `submission_code_files` 中的题号、入口标记、规范化源文本和 SHA-256。后端不编译或执行代码，不保存运行输入、日志、Notebook 新输出或代码产物；含代码作业运行时核对由教师在批改检查点环节在本地完成，运行输出不作为服务端证据。`submission_code_input_files`、执行状态、产物和 `visual_reviews` 仅作为历史数据只读保留，新评分上下文不会读取它们。

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

`DELETE /api/submissions` 仅接受可删除终态（`awaiting_grading` / `ready_for_review` / `reviewed` / `failed`）记录，存在后台处理中记录时整批原子拒绝。数据库先删记录（级联删 `submission_code_files`），提交成功后再清理学生 PDF；**共享题目 PDF 始终保留**。文件删除失败仅记录 warning，数据库是删除结果的权威来源。

### 题目级联删除

`DELETE /api/questions` 要求输入完整题目名称确认，且题目下存在处理中作业时拒绝。删除题目时事务内显式删除其关联作业与源码文件（CASCADE），并清理题目 PDF 与学生 PDF。

### 题目新版替换

新版 PDF 先进入 `question_replace` 队列，处理期间题目冻结（`replacement_status=pending/processing`）。OCR 成功后在事务内删除旧批改记录并原子切换 `file_path` / `ocr_text` / `original_filename`；因新 OCR 文本哈希变化，旧题目提取 rubric 快照一并失效（`extracted_rubric*` 置空，下次评分重新提取）。OCR 业务失败（`BusinessError`）时旧题目与旧 OCR 保持不变，仅 `replacement_status=failed`，并清理暂存 PDF，不污染原题目。

### 作业重试

失败作业通过 `POST /api/submissions/{id}/retry` 在原记录上重新入队，可选替换学生 PDF。重试会清空旧 OCR、评分建议、分数与审核数据，并重置批改评分句柄；若原 PDF 已被定期清理则必须重新上传。

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

作业：

```text
pending → ocr_processing → ocr_done → awaiting_grading
  → ready_for_review → reviewed

任一处理阶段可进入 failed
```

- OCR 完成后进入 `awaiting_grading`，等待教师在评审页发起批改 run；批改 agent 保存建议后进入 `ready_for_review`，教师在网页表单式人工复核并确认后写入 `reviewed`。
- `submissions.grading_mode` 固定为 `agent`；`grading_revision` 从 0 开始，每次成功保存建议递增；`graded_at` 为最近一次建议保存时间。`awaiting_grading` 不会自动超时；批改 run 失败后可在评审页重新发起。

## 文件生命周期

- 题目和学生 PDF 通过 `file_sha256` 映射到 `uploads/documents/<hash 前两位>/<hash>.pdf`。
- 同内容文件共享实体路径；同名但内容不同的文件保留为不同实体，原始文件名仍由记录保存。
- PDF 永久保留；删除作业或替换题目时，数据库提交成功后只清理没有任何题目、替换或作业引用的实体文件。
- 临时目录和无引用孤儿文件由维护任务清理；清理前校验真实路径位于 `UPLOAD_DIR` 内。
- 文件删除失败记录具体路径并允许维护命令重试，数据库事务不会因文件删除失败而回滚。

## 数据敏感性

- `system_config` 包含外部服务密钥（PaddleOCR token 与模型通道 API key），属于敏感数据；写入接口只写不读，API 响应仅回显后四位，不应出现在日志或版本控制中。
- 学生作业、OCR 文本与评分可能包含个人信息，应限制数据库、备份和上传目录的访问权限。
