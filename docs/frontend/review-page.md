# 协同评分页（ReviewPage）设计

## 页面定位

`/review/:id` 是教师启动 AI 批改、核对评分建议并确认最终成绩的核心页面。页面要求：

- 左栏展示学生作业 PDF（原生渲染）与提交代码证据。
- 右栏按作业状态分支：`awaiting_grading` 显示 AI 批改面板
  （`AgentRunPanel`，选通道/模型/强度后直接「开始批改」）；批改完成后显示
  评分来源审计、建议评分与表单式人工改分（`ManualReviewPanel`）。
- 评分由**后端内嵌 agent** 在进程内完成（见
  [langgraph-grading.md](../architecture/langgraph-grading.md)），教师在此确认最终成绩。

## 布局

- **桌面端**：左右两栏（PDF / 批改·复核面板），中间 9px 可拖拽分隔条；
  左栏宽度 25%–65%（默认 40%），右栏 `minmax(0, 1fr)` 自动填充。
  分隔条支持键盘方向键微调（`role="separator"` + aria 值）。
- **移动端**：垂直堆叠（`grid-cols-1`，lg 断点以上才启用分栏）。
- **全屏模式**：`MainLayout` 对 `/review/:id` 隐藏顶部 header 并去除内容 padding，
  页面自身 `h-svh` + 内部滚动。
- 顶栏单行：返回按钮 + 文件名（截断）。

相关文件：

- `frontend/src/pages/ReviewPage.tsx`
- `frontend/src/components/review/AgentRunPanel.tsx`
- `frontend/src/components/review/AgentRunProgress.tsx`（运行状态、配置与检查点）
- `frontend/src/components/review/AgentRunCodeTests.tsx`（测试集审批与结果）
- `frontend/src/components/review/AgentRunTranscript.tsx`（事件转录与分组）
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
- 加载状态使用 shimmer 骨架；加载完成后 iframe 淡入；错误状态展示空状态提示。

### AgentRunPanel（`awaiting_grading`）

三类分支（同页切换，无需跳转）：

- **无活跃 run**：显示开始面板——通道单选（官方 OpenAI API / ChatGPT 订阅，
  后者需先在设置页登录，未登录时置灰）、模型 ID 输入（占位为通道默认模型）、
  思考强度下拉（订阅通道只列 `low..max`）；通道未就绪时引导「前往设置」；
  点击「开始批改」创建 run（`POST /api/agent/runs`）。
- **运行中**（含 `waiting_for_teacher`）：`AgentRunProgress` 展示状态头、
  运行配置热切换（model/effort，下一回合生效）、执行转录（按事件 `agent` 标签
  分组并显示徽标：主管分支为「主管」，评分项分支显示对应的 rubric item ID；
  UI 侧最多保留最近 300 条事件）。`waiting_for_teacher` 时按检查点类型展示：
  `code_test_suite_approval` 显示测试集预览，并提供「批准并运行测试」和「重新生成」；
  代码与报告一致性检查点显示教师答复表单（可先在本地运行学生代码后核对，或直接确认；
  选择“不一致”必须填写说明）。其他运行状态显示「取消批改」。
- **终态**：`completed` 提示自动进入复核；`failed`/`cancelled` 展示错误与
  可重新配置发起的面板。

### AgentAuditBanner

评分建议已保存后展示于评分面板顶部：评分来源（AI 批改 / 外部助手历史兼容）、
`grading_revision` 与 rubric 来源（题目提取 / 配置 / 内置默认）。
元数据读取 `agent_metadata`（v12 及更早历史数据由 schema 桥接兼容旧键）。

### CodeEvidencePanel

- 左侧证据切换「报告」与「代码」；代码存在时展示题号、文件名与提交源代码，
  数据集区域展示输入文件名和大小。当前组件不展示源码 SHA-256。
- `submission_code_files` 保存源码文本、源码路径和 SHA-256；
  `submission_code_input_files` 保存数据集文件路径、大小和 SHA-256。
  题目级测试集版本保存在 `code_test_suites`；每次 run 使用的测试集快照与
  测试结果（状态、摘要及受限输出）保存在 `grading_run_artifacts`，并由运行面板展示。
  测试结束后清理临时工作目录，不保留学生程序生成的任意文件。

### ManualReviewPanel

表单式人工改分面板：

- **建议评分**：展示 AI 建议总分、置信度、总体反馈与逐项证据。
- **来源横幅**：`AgentAuditBanner` 显示评分来源、revision 与 rubric 来源。
- **AI 复核卡片**：`AssessmentReviewCard` 显示独立复核结论（总体 verdict、
  总结、可折叠逐项意见、时间）；建议更新后按 `reviewed_revision` 与当前
  revision 的差异标记过期。v1 仅展示，不提供发起入口。
- **表单改分**：每个评分项提供分数 Input 与评语 Textarea，教师可直接修改；
  另有总体反馈 Textarea。
- **一致性校验**：实时校验各评分项得分之和等于总分、各项满分之和等于总满分
  （`totalsMatch` / `totalMaxMatch`），不通过时禁用提交并提示。
- **确认提交**：`POST /submissions/{id}/finalize` 必填 `expected_revision`
  （非负整数），使用表单初始化时的建议版本；后端持锁刷新后拒绝过期提交，
  409 响应包含 `current_revision`，不写最终成绩。缺少版本返回 422。
- **人工草稿**：保留评分项 ID、原满分和原版本；未编辑时自动载入新建议，已编辑
  时保留草稿、提示更新并禁用提交。教师点击「载入最新建议」后替换草稿、重新
  复核。独立复核刷新不重置表单。409 后刷新状态与详情，保留未提交修改；
  另一窗口已确认成绩时切为只读。
- **审核教师姓名**：当前固定为 `Teacher` 写入 `reviewer_name`。

### FailedRecoveryPanel

`failed` 状态下展示错误原因与「使用原文件重试」入口（沿用原 submission ID）。

## CSS 工具类

定义于 `frontend/src/index.css`：

- `.score-bar-track` / `.score-bar-fill`：维度得分进度条（轨道使用 `var(--muted)`）。
- `.animate-score-card-enter` / `.animate-soft-fade-in`：进入动画。

## 数据流

- `useSubmissionStatus` 对处理中与待复核记录保持 SSE 与 30s 状态刷新；同状态
  事件也刷新详情，只有 `reviewed/failed` 停止观察；`awaiting_grading` 已启用
  `useSubmission` 拉取恢复页详情，其他处理中状态保持轻量。
- `useSubmission` 拉取完整详情；`ready_for_review` 每 30s 刷新，覆盖没有 SSE
  通知或同 revision 的独立复核更新。历史列表把待批改/待审阅显示为可审阅。
- 批改运行数据由 `api/agent.ts` 提供：`useActiveAgentRun`（404 视为无活跃
  run）、`useCreateAgentRun`、`useAgentRunEvents`（SSE `/api/agent/runs/{id}/stream`）、
  `useCancelAgentRun`、`useReplyAgentCheckpoint`、`useUpdateAgentRunConfiguration`。
- `useFinalizeSubmission` 提交教师确认的最终评分；`FinalizePayload` 由
  `ManualReviewPanel` 构造。
- 旧 ACP 对话面板（`acp-chat/*`）与 `acp-assistant-prefill` 预填机制已随
  agent-refactor 整体删除。
