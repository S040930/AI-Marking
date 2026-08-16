# 外部编程助手 MCP 评分架构

## 最短流程

任意支持 STDIO MCP 的编程助手（Codex、Claude Code、Opencode 等）通过本机 STDIO MCP 调用仅监听 loopback 的 FastAPI `/api/mcp`，教师的正常路径固定为：

```text
prepare_ai_marking_submission（只读）
→ 编程助手在当前任务临时目录尝试运行代码（结果留在对话）
→ submit_prepared_ai_marking_submission（写）
→ open_ai_marking_assignment（只读，10 秒轮询、最多 5 分钟）
→ [needs_rubric 时 save_ai_marking_question_rubric，再重新打开]
→ confirm_ai_marking_visual_review（含代码作业必需）
→ save_ai_marking_assessment（写）
```

预检在 MCP 进程内校验本机路径、PDF、多语言代码入口和文件 SHA-256，并向后端请求题目映射；成功后产生 30 分钟有效的进程内 `submission_plan`。计划不写入数据库，也不保存源码正文，只保存路径、哈希和入口元数据；每次创建或读取时清理过期项，单进程最多保留 256 个有效计划。MCP 重启或文件变化时必须重新预检。

打开作业由 MCP 在服务端状态上每 10 秒轮询，最多 5 分钟。就绪时后端提供题目、报告、源码和 `resolved_rubric`，每页最多 40,000 Unicode 字符，总上下文硬上限 200,000 字符；超过上限需拆分作业或缩减提交内容。中间页只有 `continuation_token`，完整读取的最终页才签发 `grading_handle`。句柄持久化于 PostgreSQL，绑定 revision 和 context hash，服务重启或多 worker 不会丢失。

`list_pending_ai_marking_assignments(cursor, limit)` 按上传时间与 ID 升序返回等待评分的作业（最多 100 条，带 `next_cursor` 分页），用于恢复被关闭的原任务、续接或清理待办。

## 评分与边界

评分包中的 `grading_policy.resolved_rubric` 是唯一运行时政策：题目提取快照优先，其次是题目绑定配置项目的结构化 rubric，最后是内置 rubric。所有评分结果必须逐项覆盖每个权威 item 一次，并匹配 item ID、criterion、max_score 和总分。

### 题目 rubric 提取（needs_rubric 分支）

`open_ai_marking_assignment` 返回 `needs_rubric=true` 时，评分包携带 `question_id`、`question_ocr_text` 与 `rubric_handle`，表示题目还没有可信 rubric 且配置也未提供。流程：

1. 编程助手只从 `question_ocr_text` 提取评分项与满分（不得凭空生成或沿用旧缓存）。
2. 调用 `save_ai_marking_question_rubric(question_id, handle, status, items, total_max_score)`：
   - `status=complete`：逐项提交 `criterion` / `max_score` / `details`，每项 `source_quote` 必须是题目 OCR 的原文子串且包含该项满分，`total_max_score` 等于各项满分之和。
   - `status=absent_or_ambiguous`：题目没有明确 rubric，服务端改走配置 rubric 或内置默认；`items` 置空。
3. 服务端以确定性规则校验（引用子串、满分包含、总分一致性、评分项不重复），通过后写入题目级权威快照并返回 `rubric_snapshot_id`，随后删除该次提取句柄；校验失败返回 422，不写入。客户端不得绕过或降级该项校验。
4. 保存成功后重新调用 `open_ai_marking_assignment` 以新 rubric 快照评分。

代码支持 Python/Notebook、R、Java、C、C++；每题一个入口，可附题目要求的同题辅助源码/头文件。编程助手在提交前把同题源码复制到独立临时目录中尝试运行，不修改原文件、不申请提权、不开放网络。运行失败、超时或本机缺少语言环境都由编程助手明确告知教师，但不阻止上传和静态评分；运行输出不上传后端，不进入评分包，不作为服务端证据。

含代码作业必须在评分前调用 `confirm_ai_marking_visual_review`，确认编程助手运行表现与报告描述一致，或提交带说明的不一致结论；无代码作业调用该工具返回 409。评分证据只允许服务端可验证的 `source_line` 和 `report_quote`，拒绝 `run_log`、代码产物、`visual_comparison` 和其他视觉输入。教师仍须在网页确认最终成绩。

MCP 不访问数据库、不暴露删除、重试、配置或最终确认工具。保存请求必须带 request ID、revision、context hash、rubric snapshot ID 和逐项 rubric item ID；相同 request ID 的精确重放返回原结果，不同内容冲突返回 409。

保存请求可携带 `client` 字段标识评分来源（如 `codex`、`claude-code`、`opencode`），由启动脚本通过 `AI_MARKING_MCP_CLIENT` 环境变量自动注入，后端记录在 `mcp_metadata.client` 供网页审计展示；未提供时按「外部编程助手」显示。

## 运行与诊断

```bash
./scripts/setup-mcp --client codex    # 或 claude / opencode
./start.sh
./scripts/setup-mcp --client codex --doctor
```

诊断验证 MCP API v10、服务身份和数据库；不检查代码运行器或后端工具链。题目 rubric 由编程助手从 OCR 提取、服务端确定性校验后写入权威快照，MCP 保存评分时只能引用当前快照，不能上传自由文本 rubric。FastAPI 与 MCP 服务升级 API 版本后必须同时重启，不提供旧版本兼容 shim。
