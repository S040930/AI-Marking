# 协同评分页（ReviewPage）设计

## 页面定位

`/review/:id` 是教师与 AI 协同完成最终评分的核心页面。页面要求：

- 左侧展示学生作业 PDF（非全文字，原生渲染）。
- 右侧为 AI 聊天式评分助手。
- AI 给出评分与详细理由；教师输入给分/理由后，AI 据此校准并标定最终得分。

## 布局

- **桌面端**：左右分栏，左侧占 55%，右侧占 45%，中间以 `border-border` 分隔。
- **移动端**：垂直堆叠，PDF 在上，聊天面板在下。
- **顶部空间压缩**：ReviewPage 不再保留独立 header 和 PDF 切换栏；返回按钮、文件名、状态 badge、PDF 切换全部整合到 `PdfViewer` 的 toolbar 中，仅保留一行高度。
- **整体高度**：占满 MainLayout 内容区，使用 `-m-8` 抵消默认 `p-8` padding，形成沉浸式工作区。

相关文件：

- `frontend/src/pages/ReviewPage.tsx`
- `frontend/src/components/PdfViewer.tsx`
- `frontend/src/components/ReviewChatPanel.tsx`

## 视觉风格

采用与全局一致的 **Indigo 浅色极简设计系统**：

- 以净白/浅灰为底（`bg-background`、`bg-card`、`bg-muted`）。
- Indigo 主色作为功能强调（总分、置信度 badge、确认按钮、进度条）。
- 消息气泡、评分卡片均使用 `bg-card` + `border-border`，不再使用奶油色/暖灰色。
- 支持 `prefers-reduced-motion` 动画降级。

## 关键组件

### PdfViewer

- 通过 iframe 加载 `/api/submissions/{id}/pdf?type=submission|question`。
- toolbar 为单行：返回按钮 + 文件名（截断）+ 状态 badge + PDF 切换 tab。
- 移动端在 toolbar 下方展示单独的 tab fallback。
- 加载状态使用 shimmer 骨架；加载完成后 iframe 淡入。
- 错误状态展示空状态提示。

### ReviewChatPanel

- **顶部评分摘要条（ScoreSummaryBar）**：
  - 固定显示在聊天面板顶部，显示 AI 初步评分、满分、置信度、一行 feedback。
  - 右侧“查看详情 / 收起”按钮可展开完整 `ScoreInsightCard`。
  - 摘要条与详情卡片均不随消息滚动，避免被底部输入区遮挡。
- **消息列表**：
  - AI 消息：`bg-card` + `border-border` 圆角气泡。
  - 用户消息：`bg-primary` 圆角气泡。
  - 移除固定的 AI 欢迎语，减少顶部占用。
- **ScoreInsightCard**：
  - 顶部展示总分与置信度。
  - 中部展示总体反馈（可展开）。
  - 底部以进度条可视化各维度得分，默认展示前 3 项，可展开全部。
- **快捷操作**："采纳当前评分"、"要求 AI 复核"。
- **输入区**：大圆角 textarea + 圆形发送按钮；Enter 直接发送，Shift+Enter 换行。
- **最终评分确认（FinalizeConfirmCard）**：当 AI 返回 `action=finalize` 时展示，教师确认后提交。
- **审核教师姓名**：不在本页输入，统一从系统配置的 `operator_name` 读取（Settings 中维护）。

## CSS 工具类

定义于 `frontend/src/index.css`：

- `.message-bubble-ai` / `.message-bubble-user`：消息气泡阴影与悬浮效果。
- `.score-bar-track` / `.score-bar-fill`：维度得分进度条（轨道使用 `var(--muted)`）。
- `.animate-message-enter` / `.animate-score-card-enter` / `.animate-soft-fade-in`：进入动画。

## 数据流

- `useSubmissionStatus` 轮询处理状态，终态后启用 `useSubmission` 拉取详情。
- `useConfig` 读取 `operator_name` 作为 `reviewer_name`。
- `useConversations` 获取历史对话；`useChat` 发送教师消息并接收结构化响应。
- `useFinalizeSubmission` 提交教师确认的最终评分。
- AI 返回 `action=finalize` 时前端显示确认卡片，由教师二次确认后写入数据库。
