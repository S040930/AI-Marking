# MCP v10 评分保存契约

`open_ai_marking_assignment` 返回的评分包是唯一评分依据。打开作业时先处理 `needs_rubric` 分支（见下），再完整读取评分包至 `context_complete=true`，并逐项使用服务端返回的 `rubric_item_id`、`criterion` 和 `max_score`；不能从题目文本、学生作业或旧缓存自行生成 rubric。

## Rubric 来源（v10）

rubric 按以下顺序生效，最终以评分包 `grading_policy` 中的 `resolved_rubric` 为准：

1. **题目提取**（`question_extracted`）：`open_ai_marking_assignment` 返回 `needs_rubric=true` 时，先调用 `save_ai_marking_question_rubric` 从 `question_ocr_text` 提取并保存，再重新打开作业。
   - `status=complete` 时逐项提交 `criterion` / `max_score` / `details`，每项 `source_quote` 必须是题目 OCR 的原文子串且包含该项满分，`total_max_score` 等于各项满分之和。
   - `status=absent_or_ambiguous` 表示题目没有明确 rubric，服务端改走配置或内置默认。
   - 服务端确定性校验通过后才写入题目级权威快照并返回 `rubric_snapshot_id`；校验失败返回 422，不写入。
2. **服务端配置**（`configured`）：系统设置中配置的结构化 rubric。
3. **内置默认**（`built_in_default`）：以上都没有时使用。

## 保存建议

`save_ai_marking_assessment` 的 `assessment` 必须包含：

- `request_id`：本次保存请求的 UUID；重试时必须精确复用。
- `rubric_source` 与 `rubric_snapshot_id`：必须与评分包完全一致（`question_extracted` / `configured` / `built_in_default` 之一）。
- 总 `score`、`max_score`、`confidence`、`feedback`。
- 覆盖每个权威 rubric item 一次且仅一次的 `details`，每项包含匹配的 `rubric_item_id`、`criterion`、`max_score`、得分、评语和服务端可定位证据。
- `self_check`：列出实际复核的条目、发现的问题、修正内容；复核开启（`grading_policy.review_required=true`）时必须声明 `second_pass_completed=true` 且 `rubric_items_reviewed` 非空。

证据只允许引用服务端可验证的 `source_line` 和 `report_quote`。`run_log`、代码产物、`visual_comparison`、`visual_reviews` 以及其他视觉输入都不是合法的新评分证据。

含代码作业在评分前必须调用人工一致性确认工具：使用者确认编程助手本地运行表现与报告一致，或提交带非空说明的 `mismatch`。确认结果只通过 grading handle 传递并写入审计元数据，不放入 assessment 自由字段。无代码作业不调用该工具。

不要提交 `question_rubric`、自由 rubric 文本、`rubric_used`、`expected_revision`、`context_hash`、模型型号或未知字段；这些字段由 MCP v10 服务端生成、校验或明确禁止。旧版本请求不会兼容，服务端会返回结构校验错误。
