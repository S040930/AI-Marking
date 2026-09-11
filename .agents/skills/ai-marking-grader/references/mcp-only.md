# MCP 模式专属附录（ai-marking-grader）

本文件是 MCP 模式专属附录，仅 **MCP·新作业**（上传）与 **MCP·已存在作业**（list_pending 恢复）时读取。**ACP 模式**无需本文件。

## 新作业上传（评分流程步骤 1-4）

1. **解压作业 zip**：把学生在对话中上传的 zip 解压到当前任务的临时目录，找出**唯一的报告 PDF** 和全部代码文件。代码扩展名：`.py` / `.ipynb` / `.r` / `.java` / `.c` / `.cc` / `.cpp` / `.cxx` 及配套头文件。
2. **本地运行代码**：在提交前，把每个代码入口和题目要求的输入复制到独立临时目录中尝试运行，记录运行表现（能否运行、输入输出、报错、用时）。不修改原文件、不申请提权、不开放网络。运行失败、超时或本机缺少语言环境，都要明确告知教师，但不阻止上传和静态评分。运行结果只保留在当前对话，**不上传后端**。
3. **准备提交**：调用 `prepare_ai_marking_submission(question_name=<题目名>, report_path=<报告绝对路径>, code_file_paths=[<代码绝对路径>...])`。
   - 返回 `needs_question_choice` 时：只向教师展示候选题目并等待其选择，**不要上传**。
   - 返回 `ready_to_submit` 时：使用返回的 `submission_plan`。
   - 非 `q<题号>.<扩展名>` 命名的代码文件，需通过 `code_mappings` 显式指定题号。
   - 每题必须标记且只能标记一个入口文件，可附带同题辅助源码/头文件。
4. **上传作业**：调用 `submit_prepared_ai_marking_submission(submission_plan)`，使用返回的 `submission_id` 打开作业。

上传输入约束：仅允许题目 OCR 明确列出的 UTF-8 CSV 作为只读输入；不要上传 ZIP、依赖文件、辅助模块或其他数据文件。

## 发现待办作业（list_pending）

`list_pending_ai_marking_assignments(cursor, limit)` 按上传时间与 ID 升序返回等待评分的作业（最多 100 条，带 `next_cursor` 分页）。用于恢复：原任务关闭、续接或清理待办时，用它发现 `awaiting_mcp` 的作业，再逐个 `open_ai_marking_assignment` 打开。