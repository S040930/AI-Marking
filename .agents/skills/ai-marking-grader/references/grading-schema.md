# 保存评分建议

`save_ai_marking_assessment` 的 `assessment` 只包含：`rubric_source`、仅题目 OCR rubric 使用的 `question_rubric`、总分、置信度、总体反馈、逐项 `details`、空的 `visual_reviews` 和真实 `self_check`。人工一致性核验答复只在当前对话中使用，不写入 assessment。

- 每项必须有唯一评分项、分数、满分、可执行评语和可定位 evidence；所有单项必须加总为总分。
- 代码作业只使用 `report_quote`、`source_line` 和 `run_log` 结构化引用；当前流程不使用 `visual_comparison`，也不生成图片结论。人工说明的不一致用于本次判分，但 evidence 仍必须引用服务端可定位的报告文字、源代码或运行日志。
- `self_check` 必须记录实际复核的评分项、发现的问题和修正，不能使用空泛占位语。
- 不要提供 `request_id`、`expected_revision`、`context_hash`、`rubric_used` 或任何模型型号；这些由 MCP 和服务端处理。
