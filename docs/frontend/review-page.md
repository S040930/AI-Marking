# 协同评分页（ReviewPage）设计

## 页面定位

`/review/:id` 是教师核对编程助手评分建议并确认最终成绩的核心页面。页面要求：

- 左侧展示学生作业 PDF（原生渲染）与提交代码证据。
- 右侧展示 MCP 评分来源审计、建议评分与表单式人工改分（`ManualReviewPanel`）。
- 后端不再内置 LLM/Agent，不存在教师—AI 对话；编程助手只保存建议，教师在此确认最终成绩。

## 布局

- **桌面端**：左右分栏，左侧默认占 55%，右侧 45%，中间以 `border-border` 分隔；教师可拖动分隔条调整（35%–70%）。
- **移动端**：垂直堆叠，PDF 在上，评分面板在下。
- **顶部空间压缩**：ReviewPage 不再保留独立 header；返回按钮、文件名、状态 badge、PDF/代码切换全部整合到 PDF 工具栏中，仅保留一行高度。
- **整体高度**：占满 MainLayout 内容区，使用 `-m-8` 抵消默认 `p-8` padding，形成沉浸式工作区。

相关文件：

- `frontend/src/pages/ReviewPage.tsx`
- `frontend/src/components/PdfViewer.tsx`
- `frontend/src/components/ManualReviewPanel.tsx`

## 视觉风格

采用与全局一致的 **Indigo 浅色极简设计系统**：

- 以净白/浅灰为底（`bg-background`、`bg-card`、`bg-muted`）。
- Indigo 主色作为功能强调（总分、确认按钮、进度条）。
- 卡片使用 `bg-card` + `border-border`，不使用暖灰色。
- 支持 `prefers-reduced-motion` 动画降级。

## 关键组件

### PdfViewer

- 通过 iframe 加载 `/api/submissions/{id}/pdf?type=submission|question`。
- toolbar 为单行：返回按钮 + 文件名（截断）+ 状态 badge + PDF/代码切换 tab。
- 移动端在 toolbar 下方展示单独的 tab fallback。
- 加载状态使用 shimmer 骨架；加载完成后 iframe 淡入；错误状态展示空状态提示。

### McpWaitingPanel

`awaiting_mcp` 状态下的占位面板：说明作业 OCR 已完成、等待编程助手评分，展示可复制的恢复指令，并提示“编程助手只会保存评分建议；最终成绩仍需教师回到此网页确认”。若原任务已关闭，教师可复制指令给编程助手或使用待办列表工具发现作业。

### McpAuditBanner

评分建议已保存后展示于评分面板顶部：`mcp_metadata.client` 客户端标签（Codex / Claude Code / Opencode）、`grading_revision` 与 rubric 来源（题目提取 / 配置 / 内置默认）。

### CodeEvidencePanel

- 右侧证据分栏切换「报告」与「代码」；代码存在时展示 `submission_code_files`：题号、文件名、SHA-256，可展开查看提交源代码。
- 明确提示：新作业由编程助手在当前任务中运行和核验，后端只保存源码与 SHA-256，不保存运行产物。

### ManualReviewPanel

表单式人工改分面板，取代旧的 AI 聊天面板：

- **建议评分**：展示 MCP 建议总分、置信度、总体反馈与逐项证据。
- **表单改分**：每个评分项提供分数 Input 与评语 Textarea，教师可直接修改；另有总体反馈 Textarea。
- **一致性校验**：实时校验各评分项得分之和等于总分、各项满分之和等于总满分（`totalsMatch` / `totalMaxMatch`），不通过时禁用提交并提示。
- **确认提交**：通过 `onFinalize(FinalizePayload)` 调用 `POST /submissions/{id}/finalize`；提交成功后跳转结果页。
- **审核教师姓名**：当前固定为 `Teacher` 写入 `reviewer_name`。

## CSS 工具类

定义于 `frontend/src/index.css`：

- `.score-bar-track` / `.score-bar-fill`：维度得分进度条（轨道使用 `var(--muted)`）。
- `.animate-score-card-enter` / `.animate-soft-fade-in`：进入动画。

## 数据流

- `useSubmissionStatus` 通过 SSE 获取状态并以 30s 低频兜底；`awaiting_mcp` 已启用 `useSubmission` 拉取恢复页详情，其他处理中状态保持轻量。
- `useSubmission` 拉取完整详情（`assessment_suggestion`、`mcp_metadata`、`code_files` 等）。
- `useFinalizeSubmission` 提交教师确认的最终评分；`FinalizePayload` 由 `ManualReviewPanel` 构造。
- 历史对话接口（`/conversations`、`/chat`）与 `ReviewChatPanel` 已随 Agent 链路删除。
