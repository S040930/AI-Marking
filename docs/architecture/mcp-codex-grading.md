# Codex MCP 评分架构

## 最短流程

Codex 通过本机 STDIO MCP 调用仅监听 loopback 的 FastAPI `/api/mcp`，教师的正常路径固定为：

```text
prepare_ai_marking_submission（只读）
→ Codex 在当前任务临时目录尝试运行代码（结果留在对话）
→ submit_prepared_ai_marking_submission（写）
→ open_ai_marking_assignment（只读，10 秒轮询、最多 5 分钟）
→ confirm_ai_marking_visual_review（含代码作业必需）
→ save_ai_marking_assessment（写）
```

预检在 MCP 进程内校验本机路径、PDF、多语言代码入口和文件 SHA-256，并向后端请求题目映射；成功后产生 30 分钟有效的进程内 `submission_plan`。计划不写入数据库，也不保存源码正文，只保存路径、哈希和入口元数据；每次创建或读取时清理过期项，单进程最多保留 256 个有效计划。MCP 重启或文件变化时必须重新预检。

打开作业由 MCP 在服务端状态上每 10 秒轮询，最多 5 分钟。就绪时后端提供题目、报告、源码和 `resolved_rubric`，每页最多 40,000 Unicode 字符，总上下文硬上限 200,000 字符；超过上限需拆分作业或缩减提交内容。中间页只有 `continuation_token`，完整读取的最终页才签发 `grading_handle`。句柄持久化于 PostgreSQL，绑定 revision 和 context hash，服务重启或多 worker 不会丢失。

## 评分与边界

评分包中的 `grading_policy.resolved_rubric` 是唯一运行时政策：题目可信 OCR 快照优先，其次是题目绑定配置项目的结构化 rubric，最后是内置 rubric。所有评分结果必须逐项覆盖每个权威 item 一次，并匹配 item ID、criterion、max_score 和总分。

代码支持 Python/Notebook、R、Java、C、C++；每题一个入口，可附题目要求的同题辅助源码/头文件。Codex 在提交前把同题源码复制到独立临时目录中尝试运行，不修改原文件、不申请提权、不开放网络。运行失败、超时或本机缺少语言环境都由 Codex 明确告知教师，但不阻止上传和静态评分；运行输出不上传后端，不进入评分包，不作为服务端证据。

含代码作业必须在评分前调用 `confirm_ai_marking_visual_review`，确认 Codex 运行表现与报告描述一致，或提交带说明的不一致结论；无代码作业调用该工具返回 409。评分证据只允许服务端可验证的 `source_line` 和 `report_quote`，拒绝 `run_log`、代码产物、`visual_comparison` 和其他视觉输入。教师仍须在网页确认最终成绩。

MCP 不访问数据库、不暴露删除、重试、配置或最终确认工具。历史执行字段和历史产物仅供网页只读查看，不进入新的评分上下文。保存请求必须带 request ID、revision、context hash、rubric snapshot ID 和逐项 rubric item ID；相同 request ID 的精确重放返回原结果，不同内容冲突返回 409。

## 运行与诊断

```bash
./scripts/setup-codex-mcp
./start.sh
./scripts/setup-codex-mcp --doctor
```

诊断验证 MCP API v8、服务身份和数据库；不检查代码运行器或后端工具链。题目 OCR 阶段由后端生成并校验结构化 rubric 快照，MCP 保存时只能引用当前快照，不能上传自由文本 rubric。FastAPI 与 Codex MCP 升级 v8 后必须同时重启，不提供 v7 兼容 shim。
