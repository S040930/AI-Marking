# UI 层次感与浅色装饰升级计划

## Summary

在现有“清新极简高亮”风格基础上，通过引入更丰富的浅色渐变、背景光晕、玻璃拟态分隔层与卡片头部装饰，提升整体界面的层次感与高级感。本次升级不新增页面或业务逻辑，仅对 `index.css`、布局与 4 个核心页面进行视觉层优化，保持简洁克制。

## Current State Analysis

已读取的关键文件：

- `/Users/mac/Desktop/AI-Marking/frontend/src/index.css`：定义了 CSS 变量、`.main-canvas` 网格背景、`.elevated-card` 卡片阴影、`.btn-primary-gradient` 主按钮渐变、`upload-pulse` 动画。
- `/Users/mac/Desktop/AI-Marking/frontend/src/layouts/MainLayout.tsx`：侧边栏 + 顶部 Header + 主内容区布局，主内容区使用 `.main-canvas`。
- `/Users/mac/Desktop/AI-Marking/frontend/src/pages/UploadPage.tsx`：单卡片上传页，卡片头部使用 `from-primary/5 to-accent/20` 渐变。
- `/Users/mac/Desktop/AI-Marking/frontend/src/pages/HistoryPage.tsx`：表格历史记录页，表头 `bg-muted/50`、空状态圆形背景。
- `/Users/mac/Desktop/AI-Marking/frontend/src/pages/ResultPage.tsx`：结果展示页，总分区域使用 `from-primary/10 to-accent/20` 渐变，多个卡片头部使用 `from-primary/5 to-transparent`。
- `/Users/mac/Desktop/AI-Marking/frontend/src/pages/SettingsPage.tsx`：表单设置页，4 个卡片头部均使用 `from-primary/5 to-accent/20`。

当前问题：

1. 背景仅依赖细网格，层次单一，缺乏高级感的“空间纵深感”。
2. 卡片头部渐变均为同一套 `primary/5 → accent/20`，重复率高，视觉疲劳。
3. 缺少浅色装饰元素（光晕、渐变 blob、毛玻璃分隔），整体偏“平”。
4. 侧边栏与主内容区边界仅靠一条边框，过渡生硬。
5. 状态徽章、空状态、加载态等辅助元素使用纯色块，缺少轻盈感。

## Proposed Changes

### 1. 全局色彩系统微调（`frontend/src/index.css`）

- 引入 3 组新的浅色装饰变量，供卡片头部和背景使用：
  - `--decorative-1`: 极淡靛蓝 `oklch(0.96 0.02 270 / 0.45)`
  - `--decorative-2`: 极淡青色 `oklch(0.96 0.025 200 / 0.4)`
  - `--decorative-3`: 极淡紫蓝 `oklch(0.96 0.02 300 / 0.35)`
- 将 `--secondary` 微调为更通透的 `oklch(0.97 0.012 270)`，让次要背景更轻盈。
- 新增 `.glass-panel` 工具类：半透明白底 + 背景模糊 + 极细边框，用于 Header、侧边栏底部和卡片叠加层。
- 新增 `.decorative-orb` 工具类：通过 `radial-gradient` 生成柔和的浅色圆形光晕，作为背景装饰。
- 扩展 `.main-canvas`：在现有网格之上叠加一层极淡的径向渐变光晕（使用 `--decorative-*`），营造空间层次。
- 新增 `.card-header-1` / `.card-header-2` / `.card-header-3` 三类头部渐变，替换目前统一的 `from-primary/5 to-accent/20`。

### 2. 布局层优化（`frontend/src/layouts/MainLayout.tsx`）

- 侧边栏 `Sidebar`：背景改用 `bg-gradient-to-b from-sidebar to-background`，底部叠加极淡的 `.decorative-orb` 装饰，弱化边框存在感。
- 顶部 Header：使用 `.glass-panel` 类，背景从 `bg-card/50` 升级为半透明白 + 模糊效果，增强高级感。
- 页面标题区：为每个页面在 `h1` 上方增加一条细长的浅色渐变装饰条（`w-12 h-1 rounded-full bg-gradient-to-r from-primary/40 to-accent/40`），作为视觉锚点。

### 3. 上传页优化（`frontend/src/pages/UploadPage.tsx`）

- 卡片头部改用 `.card-header-1`（靛蓝 → 透明渐变）。
- 拖拽区域：默认状态增加 `.decorative-orb` 背景装饰，hover 时由 orb 向外扩散；拖拽激活时叠加一层 `bg-gradient-to-b from-primary/5 to-transparent`。
- 已选文件条：背景从 `bg-muted/40` 改为 `.glass-panel`，增加轻量毛玻璃质感。

### 4. 历史记录页优化（`frontend/src/pages/HistoryPage.tsx`）

- 卡片头部改用 `.card-header-2`（青色 → 透明渐变）。
- 表格容器：外层增加 `.glass-panel` 包裹，表头从 `bg-muted/50` 改为 `bg-gradient-to-b from-muted/60 to-muted/30`。
- 状态徽章：
  - `done`：背景改为 `bg-emerald-50/80 backdrop-blur-sm`，增加一层淡淡的玻璃感。
  - `processing`：呼吸点外围增加柔和光晕。
- 空状态：圆形背景改为 `.decorative-orb` 光晕，按钮使用主渐变。
- 分页区：按钮 hover 时增加浅色背景渐变。

### 5. 结果页优化（`frontend/src/pages/ResultPage.tsx`）

- 总分卡片：从 `from-primary/10 to-accent/20` 升级为带有 `.decorative-orb` 背景的玻璃面板，分数下方增加细渐变装饰线。
- 三个内容卡片头部分别使用 `.card-header-1`、`.card-header-2`、`.card-header-3`，形成明显的视觉区分。
- 详细评分项的分数标签：背景从 `bg-primary/10` 改为 `bg-gradient-to-br from-primary/10 to-accent/15`。
- OCR 折叠区：折叠触发器使用 `.glass-panel` 样式，hover 时显示 subtle 阴影。

### 6. 设置页优化（`frontend/src/pages/SettingsPage.tsx`）

- 4 个配置卡片头部分别使用 `.card-header-1` / `.card-header-2` / `.card-header-3` / `.card-header-1`（循环），避免重复。
- 表单输入框 focus 时 ring 增加轻微的发光效果（`shadow-[0_0_0_4px_var(--ring)/0.15]`）。
- 保存按钮区域：使用 `.glass-panel` 包裹，增强底部操作区的视觉稳定感。

## Assumptions & Decisions

- **保持 shadcn/ui + Tailwind CSS v4 技术栈不变**，所有新增样式以 CSS 工具类和自定义类形式实现，不引入额外依赖。
- **浅色装饰优先使用 `oklch` 透明色**，确保在不同显示器下都保持柔和、不过饱和。
- **不启用暗色模式专属装饰**：暗色模式保持现有变量，本次聚焦浅色模式的高级感提升。
- **装饰元素均为非功能性视觉层**，不影响现有交互、表单校验、数据流。
- **动画保持克制**：仅使用 CSS transition 和已有的 `upload-pulse`，不新增复杂动效库。

## Verification Steps

1. 在 `/Users/mac/Desktop/AI-Marking/frontend` 目录运行 `npm run build`，确认无 TypeScript 与 Vite 构建错误。
2. 运行 `npm run lint`，确认无 lint 报错。
3. 运行 `npm run dev`，手动检查以下页面与元素：
   - 上传页：拖拽区域 hover/激活时是否有浅色光晕反馈，已选文件条是否有毛玻璃质感。
   - 历史记录页：表格表头、状态徽章、空状态是否呈现更丰富的浅色层次。
   - 结果页：总分卡片是否有光晕背景，三个内容卡片头部颜色是否有区分。
   - 设置页：4 个卡片头部渐变是否有节奏变化，保存按钮区域是否有玻璃面板效果。
   - 侧边栏与 Header：是否有柔和的过渡与玻璃拟态效果。
4. 检查响应式布局：在窄屏下确认装饰元素不会遮挡内容或产生水平滚动条。
