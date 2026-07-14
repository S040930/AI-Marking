# 数据模型

## 表与职责

| 表 | 核心数据 | 生命周期 |
|---|---|---|
| `questions` | 题目 PDF、OCR 文本、初始 OCR 与新版替换状态 | 可被多份作业复用；新版成功后切换 |
| `submissions` | 学生 PDF 路径、作业 OCR、评分、复核与人工审核结果 | 必须关联一个题目；终态记录可单独删除 |
| `conversations` | 教师与 AI 针对一份作业的消息 | 随 submission 通过外键级联删除 |
| `background_jobs` | 待执行、运行中或死信的题目 OCR/作业批改任务 | 成功后删除；随业务实体级联删除 |
| `system_config` | OCR、LLM、rubric 和操作人配置 | 独立于业务记录长期保留 |
| `alembic_version` | 当前数据库迁移版本 | 由 Alembic 管理 |

## 关系

```text
questions 1 ────── N submissions 1 ────── N conversations
    │                    │
    └──── 0..1 background_jobs 0..1 ─────┘
```

- `submissions.question_id → questions.id` 使用 `ON DELETE RESTRICT`，题目危险操作由应用层锁定记录、检查处理状态并在事务内显式删除关联作业。
- `conversations.submission_id → submissions.id` 使用 `ON DELETE CASCADE`。
- `background_jobs` 每行只允许关联一个题目或一份作业；两个外键均使用 `ON DELETE CASCADE`。

## 后台任务状态

```text
queued → running → 成功后删除
            ├─ 业务失败(BusinessError/AgentError) → 直接标终态 + 删除任务(不重试)
            ├─ 系统失败且未耗尽 → queued（指数退避）
            ├─ 系统失败耗尽 → dead（死信,需运维介入）
            └─ worker 崩溃 → 租约到期后重新领取
```

- worker 使用领取令牌完成和续租，旧 worker 不能完成已被重新领取的任务。
- API 与任务记录在同一事务内创建，不会出现业务记录成功但任务丢失。
- 任务类型包括 `question_ocr`、`question_replace` 和 `submission_marking`。
- **失败类型判定**：
  - 业务失败（配置错误 / 文件不可识别 / LLM 明确拒绝 / OCR 返回为空或结构异常 / PaddleOCR 地址配置错误）：`ocr_pdf` 与 `run_marking_agent` 抛出 `BusinessError` / `AgentError`，worker 立即标记目标终态并删除任务行，**不重试**。
  - 系统失败（网络瞬时抖动耗尽 / 数据库中断 / 进程崩溃等其余异常）：worker 保留队列退避重试，达到 `TASK_MAX_ATTEMPTS` 后转死信。
  - OCR 内部对超时 / 限流 / 5xx 进行最多 3 次短暂重试；这 3 次网络重试耗尽仍属系统失败（可重试），而配置类错误属业务失败（不重试）。
- 教师重新上传 PDF / 题目后才创建新任务，系统不会自动重跑原文件。

## 删除 / 替换 / 重试的数据生命周期

### 单条作业删除（终态）

`DELETE /api/submissions` 仅接受终态（`ready_for_review` / `reviewed` / `failed`）记录，存在处理中记录时整批原子拒绝。数据库先删记录（级联删 `conversations`），提交成功后再清理学生 PDF；**共享题目 PDF 始终保留**。文件删除失败仅记录 warning，数据库是删除结果的权威来源。

### 题目级联删除

`DELETE /api/questions` 要求输入完整题目名称确认，且题目下存在处理中作业时拒绝。删除题目时事务内显式删除其关联作业与对话（CASCADE），并清理题目 PDF 与学生 PDF。

### 题目新版替换

新版 PDF 先进入 `question_replace` 队列，处理期间题目冻结（`replacement_status=pending/processing`）。OCR 成功后在事务内删除旧批改记录并原子切换 `file_path` / `ocr_text` / `original_filename`；OCR 业务失败（`BusinessError`）时旧题目与旧 OCR 保持不变，仅 `replacement_status=failed`，并清理暂存 PDF，不污染原题目。

### 作业重试

失败作业通过 `POST /api/submissions/{id}/retry` 在原记录上重新入队，可选替换学生 PDF。重试会清空旧 OCR、AI 结果、审核数据与对话；若原 PDF 已被定期清理则必须重新上传。

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
pending → ocr_processing → ocr_done → agent_grading
  → agent_reviewing → [agent_revising → agent_grading]
  → ready_for_review → reviewed

任一处理阶段可进入 failed。
```

## 文件生命周期

- 题目 PDF 路径存储于 `questions.file_path`，定期清理任务会保护仍被题目表引用的文件。
- 学生 PDF 路径存储于 `submissions.file_path`，删除作业后在数据库提交成功后清理。
- 定期清理会删除超过保留期且不受保护的 PDF；数据库中的 OCR 文本和评分记录继续保留。
- 文件删除失败只记录 warning，数据库是删除结果的权威来源。

## 数据敏感性

- `system_config` 当前包含外部服务密钥，属于敏感数据，不应出现在日志、API 明文展示或版本控制中。
- 学生作业、OCR 文本、评分与对话可能包含个人信息，应限制数据库、备份和上传目录的访问权限。
