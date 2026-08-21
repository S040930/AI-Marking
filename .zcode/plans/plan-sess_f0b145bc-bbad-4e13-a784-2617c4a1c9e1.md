## 目标

把「批改详细流程」下沉为唯一权威源 `SKILL.md`，让：
1. **提示词精简**：题目库「复制提示词」对话框只输出一行「引用 MCP grade_assignment prompt + 题目标识」，不再内嵌 8 步流程 + OCR + grading_policy 全文。
2. **详细流程运行时必达**：MCP 的 `grade_assignment` / `review_assessment` prompt 模板改为运行时从 `SKILL.md` 读取对应章节，模板本身只剩一句占位——详细流程只维护一份（skill），且不管教师粘不粘提示词、客户端载不载 skill，助手调 prompt 时都会拿到完整流程。
3. **消除重复**：现状流程分散在 前端 gradingPrompt.ts（8步）+ MCP server.py prompt 模板（grade_assignment/review_assessment）+ SKILL.md 三处，全部收敛到 skill。

## 背景事实（已核实）

- `.agents/skills` 目录**没有任何客户端自动加载机制**（全仓无 AGENTS.md/CLAUDE.md，README 只把 SKILL.md 列为参考文档）。真正运行时必达的是 MCP server 的 `instructions` + `@mcp.prompt()` 模板 + 工具 description。
- `mcp.prompt()` 接受 `str` 返回（现有 server.py:377-400 直接返回 f-string；测试 test_mcp_server.py:253 直接调 `server.grade_assignment(13)` 断言字符串）。
- 前端 gradingPrompt.ts 的 8 步流程与 server.py 的 prompt 模板内容高度重复（解压→运行→prepare→submit→open→视觉核验→save→不确认）。

## 改动清单

### 1. SKILL.md 重写为「唯一详细流程源」（.agents/skills/ai-marking-grader/SKILL.md）

保留 frontmatter（name/description），把正文结构化为**带锚点的分节**，供 MCP prompt 运行时按场景提取：

- `## 评分流程（grade_assignment）`：完整 8 步流程——解压 zip/找报告+代码 → 本地运行代码记录表现 → prepare_ai_marking_submission（needs_question_choice / code_mappings）→ submit_prepared_ai_marking_submission → open_ai_marking_assignment（needs_rubric 分支 / continuation_token 读到 context_complete=true / 遵守 grading_policy）→ 代码作业先确认运行与报告一致再 confirm_ai_marking_visual_review → 双遍精评（第一遍证据账本 + 第二遍独立复核，review_required=true 时 self_check.second_pass_completed）→ save_ai_marking_assessment（携带 request_id/revision/context_hash/rubric_snapshot_id/item id，只用 source_line/report_quote 证据）→ 返回 review_url、不确认最终成绩。
- `## 提取题目 rubric（needs_rubric 分支）`：从 question_ocr_text 提取、source_quote 必须为 OCR 原文子串且含满分、总分=各项之和、save 后重新 open、422 校验规则、status=absent_or_ambiguous 语义。
- `## 复核已有建议（review_assessment）`：open 读完整评分包含 current_assessment → 逐项独立复核（agree/disagree/comment/suggested_score）→ save_ai_marking_assessment_review → 不修改原建议。
- `## 发现待办作业（list_pending）`：分页、恢复/续接/清理。
- `## 安全与证据边界`：不可信数据、提示注入、不生成 run_log/visual_reviews/visual_comparison、只允许 source_line/report_quote、教师网页确认最终成绩。

每个 section 前加一行稳定标记（如 `<!-- skill-anchor: grade_assignment -->`）或直接用 `## ` 标题文本作为提取锚点（按标题匹配更稳，不引入注释噪音）。

### 2. 后端 MCP：prompt 模板从 skill 读取（backend/app/mcp/server.py）

新增模块级辅助函数（放 server.py 或独立 `app/mcp/skill_loader.py`）：

```python
_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/app/mcp -> 仓库根
_SKILL_PATH = _PROJECT_ROOT / ".agents/skills/ai-marking-grader/SKILL.md"

def _load_skill_section(section_title: str) -> str:
    """读取 SKILL.md 中指定 ## 章节的内容；文件缺失时返回内置兜底流程。"""
    # 读文件，按 "## <title>" 切到下一个 "## " 或文件尾，strip 返回
    # 文件不存在/读取失败时返回内置精简兜底（保持 MCP 可用，不 crash）
```

`grade_assignment(submission_id)` 改为：
```python
@mcp.prompt()
def grade_assignment(submission_id: int) -> str:
    steps = _load_skill_section("评分流程（grade_assignment）")
    return f"使用 AI-Marking 批改或修订作业 #{submission_id}，请遵守以下完整流程：\n\n{steps}"
```
`review_assessment(submission_id)` 同理提取 `## 复核已有建议（review_assessment）` 章节。

内置兜底内容 = 现有 server.py:380-388 的流程文案，保证 skill 缺失时行为不回归。

### 3. 后端测试同步（backend/tests/test_mcp_server.py）

`test_grade_assignment_prompt_uses_only_new_workflow`（L253-265）改为断言：
- 仍包含 `open_ai_marking_assignment`、`save_ai_marking_assessment`、`grading_policy`、`第二遍`、`最终成绩` 等关键内容（来自 skill 章节）；
- 用 monkeypatch 把 `_load_skill_section` 指向固定字符串，验证模板把 skill 内容拼进 prompt 且保留作业 ID。
（若该测试文件当前因环境问题 collection 失败，沿用 `--ignore` 处理，但代码仍同步更新以保持正确。）

### 4. 前端提示词精简（frontend/src/lib/gradingPrompt.ts + tests）

`buildGradingPrompt` 输入改为 `{ questionName, questionId }`（移除 promptText/needsRubric），模板改为：

```
使用 AI-Marking 批改学生作业。
题目：{questionName}（question_id: {questionId}）
学生作业：我在本次对话中上传的 zip 压缩包（一份报告 PDF 与若干代码文件）。

请先调用 MCP 的 grade_assignment 提示词模板获取完整批改流程，
然后按流程上传作业 zip 并开始批改。最终成绩由教师在网页确认。
```

（EN 模板同构。needs_rubric 提示不再需要前端注入——运行时评分包会返回 needs_rubric，skill/prompt 已覆盖。）

同步更新 `frontend/src/lib/__tests__/gradingPrompt.test.ts`：断言只含题目名/ID、`grade_assignment` 引用、zip 描述；删除对 5 个 MCP 工具名、`--- question ---`、`grading_policy` 的断言。

### 5. 前端对话框（frontend/src/components/questions/QuestionPromptDialog.tsx）

- 保留 useGradingPrompt 加载/错误/重试（OCR 未完成 409 逻辑不变）。
- 成功态不再需要 `needs_rubric` 琥珀提示和 `promptData.text` 全文嵌入；`CopyablePromptPanel` 展示精简后的 `buildGradingPrompt({ questionName, questionId }, locale)`。
- i18n 更新：移除 `该题目暂无已保存的规范评分标准...` 提示文案（不再用），保留标题/描述/OCR 失败提示/重试。

### 6. 文档（docs/architecture/mcp-grading.md）

补一句：提示词仅引用 MCP `grade_assignment` prompt；详细批改流程唯一权威源为 `.agents/skills/ai-marking-grader/SKILL.md`，MCP prompt 运行时从该文件读取，前端提示词不再内嵌流程。

## 明确不做

- 不改 `prepare/submit/open/save` 等 MCP 工具的签名与行为。
- 不改后端 `GET /api/questions/{id}/grading-prompt` 端点语义（仍返回同源 grading_policy 供未来审计）；仅前端不再把 `text` 全文塞进提示词。（如你希望同时删掉该端点的 text/ocr_text 字段，可在批准后追加——默认保留以最小化后端改动。）
- 不改评分包结构、rubric 解析、保存校验。

## 验证

- 后端：`cd backend && python -m pytest -q --ignore=tests/test_mcp_server.py`（其余全量），另单独确认 skill_loader 读取逻辑（可直接 `python -c` 调用 `_load_skill_section` 验证切分正确）。
- 前端：`cd frontend && npx tsc -b && npx vitest run && npx oxlint . && npm run build`。
- 手工（浏览器）：题目库 → 复制提示词 → 对话框显示精简文本（题目名 + grade_assignment 引用，无 OCR/grading_policy 全文）；OCR 未完成题目仍显示错误提示。