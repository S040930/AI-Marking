# UI 设计优化方案

## 1. Summary

基于当前截图反馈与代码现状，本次方案对 AI 作业批改系统前端进行**较大改版**级别的视觉优化。核心目标：

1. 建立一套更统一、清爽的色彩系统，消除 Logo、按钮、图标、激活态之间的色差与视觉冲突。
2. 降低侧边栏视觉噪音，让导航回归「背景层」定位。
3. 强化上传页拖拽区的交互引导，改善大空白区域的「没精神」问题。
4. 重新梳理卡片内部留白与页面层级，使整体排版更紧凑、更符合苹果极简风。

方案保持 React + Vite + TypeScript + Tailwind CSS v4 + shadcn/ui 技术栈不变，仅修改样式变量与组件/页面级 className。

## 2. Current State Analysis

### 2.1 已确认的现状

- 全局色彩定义在 `frontend/src/index.css`，主色 `--primary: oklch(0.62 0.18 270)`，整体偏蓝紫。
- 侧边栏 Logo 图标使用 `bg-primary text-primary-foreground`，在浅灰侧边栏上形成强焦点。
- 导航激活态图标使用 `bg-primary text-primary-foreground`，与 Logo 形成「两处高饱和色块」。
- 上传页拖拽区默认图标为 `text-muted-foreground`，Hover 才变主色，默认状态引导性弱。
- `UploadPage` 卡片头部 `pb-6` + 内容区 `pt-6`，导致标题与拖拽区留白过大。
- 状态徽章（如完成态）使用硬编码 `emerald-200/50/700`，未接入语义化色变量。
- 设置页、上传页、结果页卡片头部均使用 `bg-primary text-primary-foreground` 图标，视觉上重复且喧闹。

### 2.2 主要问题

| 问题 | 影响 |
|---|---|
| 主色在不同组件上呈现不一致 | 截图中按钮与图标蓝色有差异，视觉不精致 |
| 侧边栏 Logo + 激活态均为实心主色 | 导航变成视觉焦点，喧宾夺主 |
| 拖拽区默认状态过灰 | 用户难以感知这是核心交互区 |
| 卡片留白不均衡 | 头部与内容脱节，页面重心偏上 |
| 语义色硬编码 | 暗色模式/后续主题扩展困难 |

## 3. Design Direction

- **风格**：苹果极简风，纯色背景、单一强调色、极轻阴影、靠字重与间距建立层次。
- **主色**：将现有蓝紫偏向的 primary 调整为更清爽的冷靛蓝（`oklch(0.6 0.15 265)`），降低饱和度、略微压暗，使按钮/图标更协调。
- **辅助色**：在 `index.css` 中增加 `--success`、`--warning`、`--info` 语义变量，替换硬编码 emerald。
- **图标处理**：卡片头部与侧边栏图标统一改为「浅色主色背景 + 主色图标」（`bg-primary/10 text-primary`），避免实心色块。
- **激活态**：侧边栏激活态改为「主色淡底 + 主色文字 + 右侧 2px 指示条」，更 subtle。
- **留白**：压缩 `UploadPage` 卡片头部与内容的间距，统一所有页面 `CardHeader pb-4` + `CardContent pt-4/5`。
- **交互引导**：拖拽区默认状态使用「主色极淡底 + 主色半透明图标」，Hover 时加深；激活态使用「主色边框 + 主色底 + 图标放大」。

## 4. Proposed Changes

### 4.1 `frontend/src/index.css` — 色彩系统重构

**What/Why**：
- 调整 primary 色相与饱和度，使其更偏靛蓝、更协调。
- 新增语义化辅助色变量，统一成功/警告/信息状态。
- 调整 sidebar 背景，让它与页面背景有轻微层次但不突兀。
- 优化 border/muted/secondary 色阶，增加灰度层次。

**How**：
1. 修改 `:root` 中的 `--primary` 为 `oklch(0.6 0.15 265)`，同步更新 `--ring`、`--chart-1`、`--sidebar-primary`。
2. 新增以下变量：
   - `--success: oklch(0.65 0.14 155)` / `--success-foreground: oklch(0.2 0.05 155)`
   - `--warning: oklch(0.85 0.12 95)` / `--warning-foreground: oklch(0.3 0.05 95)`
   - `--info: oklch(0.75 0.1 245)` / `--info-foreground: oklch(0.2 0.05 245)`
3. 调整 `--sidebar: oklch(0.98 0.003 260)`，比背景稍深一级。
4. 调整 `--muted: oklch(0.95 0.01 260)`、`--border: oklch(0.89 0.01 260)`，增加层次。
5. 同步 `.dark` 下的对应变量。
6. 在 `@theme inline` 中导出新增变量。
7. 保持 `.elevated-card` 工具类，微调阴影透明度使其更轻。

### 4.2 `frontend/src/layouts/MainLayout.tsx` — 侧边栏视觉降级

**What/Why**：
- 侧边栏应作为「背景层」存在，当前 Logo 和激活态都是实心主色，视觉权重过高。

**How**：
1. Logo 图标容器从 `bg-primary text-primary-foreground` 改为 `bg-muted text-primary`。
2. 激活态导航图标从 `bg-primary text-primary-foreground` 改为 `bg-primary/10 text-primary`。
3. 激活态文字保持 `text-foreground`，非激活态文字保持 `text-muted-foreground`。
4. （可选）在激活项右侧增加 `after:absolute after:right-0 after:top-1/2 after:h-5 after:w-0.5 after:-translate-y-1/2 after:rounded-l-sm after:bg-primary` 指示条，提升可识别性。
5. 检查所有图标容器尺寸统一为 `size-7 justify-center`。

### 4.3 `frontend/src/pages/UploadPage.tsx` — 上传区重设计

**What/Why**：
- 上传是核心功能页，但拖拽区默认状态灰暗、卡片留白过大。

**How**：
1. 卡片头部 `CardHeader className="pb-4"`，内容区 `CardContent className="flex flex-col gap-5 pt-4"`。
2. 头部图标容器从 `bg-primary text-primary-foreground` 改为 `bg-primary/10 text-primary`。
3. 拖拽区默认状态：
   - 外层：`border-border bg-primary/[0.03] hover:border-primary/40 hover:bg-primary/[0.06]`
   - 图标容器：`bg-background text-primary/50 group-hover:text-primary group-hover:bg-primary/5`
4. 拖拽激活态：
   - 外层：`border-primary bg-primary/5`
   - 图标容器：`bg-primary text-primary-foreground shadow-md shadow-primary/20 scale-110`
5. 文件条保持现有结构，文件图标改为 `bg-primary/10 text-primary`。
6. "开始批改"按钮保持 `Button` default 变体（已使用 `--primary`），不再额外加样式。

### 4.4 `frontend/src/pages/HistoryPage.tsx` — 表格与状态统一

**What/Why**：
- 历史页信息密度高，需要更清晰的层次；状态色硬编码不利于维护。

**How**：
1. 表格表头 `bg-muted/50` 保持不变，表头文字使用 `text-muted-foreground font-medium`，降低对比。
2. 完成状态徽章改用新语义变量：
   - `className="border border-success/20 bg-success/10 text-success hover:bg-success/15"`
3. 处理中状态徽章保持 `text-primary`，但呼吸点动画可保留。
4. 文件名图标从 `text-primary/70` 改为 `text-primary/60`。
5. 空状态图标容器增加 `bg-muted text-muted-foreground`，保持朴素。
6. 行 Hover 保持 `hover:bg-muted/40`。

### 4.5 `frontend/src/pages/ResultPage.tsx` — 结果卡片层次优化

**What/Why**：
- 总分卡片目前只是简单边框背景，作为结果页核心信息可以稍微强化但不夸张。

**How**：
1. 完成徽章改用新的 `--success` 语义变量。
2. 总分容器从 `bg-muted/50` 改为 `bg-primary/[0.04] border-primary/10`，让分数更突出。
3. 分数数字保持 `text-primary`。
4. 详细评分项的分数标签保持 `bg-primary/10 text-primary`。
5. OCR 折叠触发器和内容区保持 `bg-muted/50 border-border`。
6. 文件信息区的文件图标改为 `text-primary/60`。

### 4.6 `frontend/src/pages/SettingsPage.tsx` — 表单卡片视觉统一

**What/Why**：
- 设置页有 4 个配置卡片，每个头部都是实心主色图标，视觉上重复且喧闹。

**How**：
1. 4 个卡片头部图标容器统一从 `bg-primary text-primary-foreground` 改为 `bg-primary/10 text-primary`。
2. 保存区底部容器 `bg-muted/50 border-border` 保持不变，但可考虑改为 sticky bottom：`sticky bottom-0` + `backdrop-blur-sm bg-background/80`（如果页面较长时更友好）。
3. 表单字段间距保持 `gap-4`，卡片间距保持 `gap-5`。
4. 输入框 focus ring 已使用 `--ring`，无需额外修改。

### 4.7 `frontend/src/components/ui/button.tsx`（可选微调）

**What/Why**：
- default 按钮当前是纯扁平色块，可考虑在浅色模式下增加极轻微阴影提升层次感。

**How**：
1. 将 `default` 变体从 `bg-primary text-primary-foreground hover:bg-primary/90` 改为：
   - `bg-primary text-primary-foreground shadow-sm shadow-primary/15 hover:bg-primary/90 hover:shadow-md hover:shadow-primary/20`
2. 暗色模式下保持扁平，避免阴影显得脏。

> 注：此项为可选，若用户偏好完全扁平可跳过。

## 5. Assumptions & Decisions

- **保持 shadcn/ui 组件不动**：所有改动通过 className 和 CSS 变量完成，不修改 shadcn 组件内部结构，便于后续升级。
- **暗色模式同步调整**：所有新增/修改的 CSS 变量都会提供 `.dark` 对应值，保证双主题一致。
- **不引入新依赖**：不新增字体、图标库或动画库，仅使用现有 Lucide 图标和 Tailwind 工具类。
- **不上线新功能**：本次为纯视觉优化，不涉及交互流程、业务逻辑或 API 改动。
- **按钮阴影为可选**：若执行时发现与极简方向冲突，可回退到扁平样式。
- **辅助色变量不强制全量替换**：本次优先替换完成状态徽章的 emerald 硬编码，其他硬编码颜色可在后续迭代中逐步迁移。

## 6. Verification Steps

1. **构建检查**：执行 `cd frontend && npm run build`，确保无 TypeScript 或 CSS 错误。
2. **代码检查**：执行 `npm run lint`，确保无 lint 警告。
3. **视觉验证**：启动开发服务器后，检查以下页面：
   - `/` 上传页：拖拽区默认/悬停/激活状态、卡片头部、按钮颜色一致。
   - `/history` 历史记录页：完成状态徽章颜色、表格层次、空状态。
   - `/result/:id` 结果页：总分卡片视觉权重、评分项标签、文件信息区。
   - `/settings` 设置页：4 个卡片头部图标、保存区。
4. **截图对比**：使用 Playwright 或其他截图工具捕获 4 个页面关键状态，与优化前对比。
5. **暗色模式检查**：在浏览器 DevTools 中切换 `prefers-color-scheme: dark`，确认侧边栏、卡片、徽章颜色正常。

## 7. Execution Order

1. 修改 `frontend/src/index.css` 建立新的色彩系统。
2. 修改 `frontend/src/layouts/MainLayout.tsx` 调整侧边栏。
3. 依次修改 `UploadPage.tsx`、`HistoryPage.tsx`、`ResultPage.tsx`、`SettingsPage.tsx`。
4. （可选）微调 `button.tsx` default 变体。
5. 运行 `npm run build` 和 `npm run lint`。
6. 启动开发服务器进行视觉验证和截图对比。
