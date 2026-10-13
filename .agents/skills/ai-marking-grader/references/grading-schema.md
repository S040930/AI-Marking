# MCP 评分保存契约（语义说明，API v12）

本文件只解释 `save_ai_marking_assessment` 的**语义**。参数与字段结构以 MCP tool 当前暴露的 JSON Schema 为唯一权威；本文件与 SKILL.md 都不复制字段清单，避免随版本漂移。

## 谁决定什么

`open_ai_marking_assignment` 返回的评分包是唯一评分依据。打开作业时先处理 `needs_rubric` 分支（见下），再完整读取评分包至 `context_complete=true`（评分句柄只在最后一页签发，“完整上下文已读”由句柄语义保证）。

客户端（编程助手）只提交它真正决定的内容：

- 逐项 `rubric_item_id`、`score`、`comment`；
- 逐项 `evidence_refs` 与 `evidence_status`；
- 总体 `confidence`、`feedback`；
- 幂等用的 `request_id`（重试时必须精确复用）。

以下内容一律由服务端从 grading handle 对应的权威 rubric 推导，客户端提交会被拒绝：

- 每项 `criterion` 与 `max_score`；
- 总 `score`（= 各项得分之和）与总 `max_score`（= resolved rubric 总分）；
- `rubric_source`、`rubric_snapshot_id`；
- `expected_revision`、`context_hash`（乐观锁与上下文版本校验字段）。

## evidence_status 语义

- `supported`（默认）：存在可引用的评分依据。得分大于 0 时必须至少一条 `evidence_refs`，且逐条可在服务端上下文中定位。
- `not_found`：完整上下文中确实找不到对应内容。必须 `score=0`、`evidence_refs=[]`，并在评语中说明依据；不得为 0 分编造证据。

## Rubric 来源

rubric 按以下顺序生效，最终以评分包 `grading_policy` 中的 `resolved_rubric` 为准：

1. **题目提取**（`question_extracted`）：`open_ai_marking_assignment` 返回 `needs_rubric=true` 时，先调用 `save_ai_marking_question_rubric` 从 `question_ocr_text` 提取并保存，再重新打开作业。
   - `status=complete` 时逐项提交 `criterion` / `max_score` / `details`，每项 `source_quote` 必须是题目 OCR 的原文子串且包含该项满分，`total_max_score` 等于各项满分之和。
   - `status=absent_or_ambiguous` 表示题目没有明确 rubric，服务端改走配置或内置默认。
   - 服务端确定性校验通过后才写入题目级权威快照并返回 `rubric_snapshot_id`；校验失败返回 422，不写入。
2. **服务端配置**（`configured`）：系统设置中配置的结构化 rubric。
3. **内置默认**（`built_in_default`）：以上都没有时使用。

## 证据类型

证据只允许引用服务端可验证的 `source_line` 和 `report_quote`（格式与示例以评分包 `grading_policy.evidence_format` 为准）。`run_log`、代码产物、`visual_comparison`、`visual_reviews` 以及其他视觉输入都不是合法的评分证据。

## 一致性检查与复核

- 含代码作业在评分前必须完成教师一致性检查点：使用者确认编程助手本地运行表现与报告一致（`consistent`），或提交带非空说明的 `mismatch`。确认结果只通过 grading handle（或 ACP run）传递并写入审计元数据，不放入 assessment 自由字段。无代码作业不需要。
- 交叉复核（`save_ai_marking_assessment_review`）是**独立可选能力**：保存评分建议从不要求先复核；复核不修改原建议，最终成绩由教师在网页确认。

## 幂等与冲突

- `request_id` 已存在时：内容与句柄完全一致 → 幂等返回原响应；不一致 → 409。
- 评分句柄有效期 30 分钟；句柄过期、上下文或建议版本变化时返回 409，须重新打开并读完评分包后再保存。
- 未知字段一律 422（`extra=forbid`）；v12 之前的旧版本请求不兼容。
