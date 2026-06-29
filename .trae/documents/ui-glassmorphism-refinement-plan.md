# UI 柔光玻璃拟态风升级计划

## Summary

将现有“清新极简高亮”风格升级为**柔光玻璃拟态风（Soft Glassmorphism）**：通过多层半透明白面板、柔和彩色光晕 blob、毛玻璃叠加与细腻边框，营造通透的层次感与高级感。装饰强度为**中度层次**——全局背景有柔和渐变 mesh 与 2-3 个光晕，卡片具备玻璃质感，但不过载。字体保持本地 PingFang/system，通过字重、字距、字号层次优化高级感。不新增依赖，不改业务逻辑。

## Current State Analysis

已读取的关键文件（基于 Phase 1 探索）：

- `/Users/mac/Desktop/AI-Marking/frontend/src/index.css`
  - 现有 CSS 变量：靛蓝主色 `oklch(0.52 0.21 270)`、青色点缀 `oklch(0.94 0.04 200)`。
  - `.main-canvas`：仅细网格点阵背景。
  - `.elevated-card`：多层 box-shadow，hover 加深。
  - `.btn-primary-gradient`：主按钮 135° 渐变 + 阴影。
  - `.upload-active`：`upload-pulse` 呼吸动画。
  - 字体：PingFang SC / Hiragino Sans GB / Microsoft YaHei，`letter-spacing: -0.01em`。

- `/Users/mac/Desktop/AI-Marking/frontend/src/layouts/MainLayout.tsx`
  - Sidebar：`bg-sidebar` 纯色，`border-r border-sidebar-border/60`。
  - Header：`bg-card/50 backdrop-blur-sm`，h-16。
  - 主内容区：`.main-canvas` + `p-6`。
  - 导航项：激活态 `bg-primary` 图标块。

- `/Users/mac/Desktop/AI-Marking/frontend/src/pages/UploadPage.tsx`
  - 单卡片，头部 `bg-gradient-to-r from-primary/5 to-accent/20`。
  - 拖拽区：`border-2 border-dashed`，hover `border-primary/40`。
  - 已选文件条：`bg-muted/40`。

- `/Users/mac/Desktop/AI-Marking/frontend/src/pages/HistoryPage.tsx`
  - 表头 `bg-muted/50`，表格容器 `border border-border/60`。
  - 状态徽章：done 用 `bg-emerald-100`，processing 有呼吸点。
  - 空状态：`bg-muted` 圆形背景。

- `/Users/mac/Desktop/AI-Marking/frontend/src/pages/ResultPage.tsx`
  - 总分区：`bg-gradient-to-br from-primary/10 to-accent/20`。
  - 三个内容卡片头部均 `from-primary/5 to-transparent`。
  - 分数标签：`bg-primary/10`。
  - OCR 折叠区：`hover:bg-muted/50`。

- `/Users/mac/Desktop/AI-Marking/frontend/src/pages/SettingsPage.tsx`
  - 4 个卡片头部统一 `from-primary/5 to-accent/20`，重复率高。
  - 保存按钮区无特殊包裹。

当前问题（用户反馈）：
1. 颜色没有层次感——所有卡片头部用同一套渐变。
2. 没有高级设计感——背景仅网格，卡片为不透明白，缺乏纵深。
3. 缺少浅色装饰——没有光晕、玻璃叠加等氛围元素。

## Proposed Changes

### 1. 全局色彩与工具类（`frontend/src/index.css`）

**新增 CSS 变量（浅色装饰色）：**
```css
--glass-bg: oklch(1 0 0 / 0.55);          /* 玻璃面板底色 */
--glass-border: oklch(1 0 0 / 0.6);       /* 玻璃边框 */
--glass-shadow: oklch(0.2 0.02 270 / 0.08); /* 玻璃阴影 */
--orb-indigo: oklch(0.7 0.15 270 / 0.18); /* 靛蓝光晕 */
--orb-cyan: oklch(0.75 0.12 200 / 0.15);  /* 青色光晕 */
--orb-violet: oklch(0.72 0.13 300 / 0.12);/* 紫蓝光晕 */
--orb-warm: oklch(0.78 0.1 80 / 0.1);     /* 暖色光晕 */
```

**重写 `.main-canvas`：** 移除网格点阵，改为纯净底色 + 3 个固定位置的柔和光晕 blob（使用 `radial-gradient` 多层叠加），营造空间氛围。
```css
.main-canvas {
  background-color: var(--background);
  background-image:
    radial-gradient(ellipse 600px 400px at 15% 10%, var(--orb-indigo), transparent 60%),
    radial-gradient(ellipse 500px 500px at 85% 30%, var(--orb-cyan), transparent 55%),
    radial-gradient(ellipse 700px 500px at 50% 90%, var(--orb-violet), transparent 65%);
  background-attachment: fixed;
}
```

**新增 `.glass-panel` 工具类：** 核心玻璃拟态容器。
```css
.glass-panel {
  background: var(--glass-bg);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--glass-border);
  box-shadow:
    0 1px 3px var(--glass-shadow),
    0 8px 24px oklch(0.2 0.02 270 / 0.04),
    inset 0 1px 0 oklch(1 0 0 / 0.5);
}
```

**重写 `.elevated-card`：** 改为玻璃面板基础样式，保留 hover 过渡但更柔和。
```css
.elevated-card {
  background: var(--glass-bg);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--glass-border);
  box-shadow:
    0 1px 3px var(--glass-shadow),
    0 8px 24px oklch(0.2 0.02 270 / 0.04),
    inset 0 1px 0 oklch(1 0 0 / 0.5);
  transition: transform 200ms ease, box-shadow 200ms ease, border-color 200ms ease;
}
.elevated-card:hover {
  box-shadow:
    0 4px 8px var(--glass-shadow),
    0 16px 40px oklch(0.2 0.02 270 / 0.06),
    inset 0 1px 0 oklch(1 0 0 / 0.6);
  border-color: oklch(1 0 0 / 0.75);
}
```

**新增 3 种卡片头部渐变（替换统一渐变）：**
```css
.card-header-indigo {
  background: linear-gradient(135deg, oklch(0.96 0.03 270 / 0.7) 0%, transparent 100%);
}
.card-header-cyan {
  background: linear-gradient(135deg, oklch(0.96 0.03 200 / 0.7) 0%, transparent 100%);
}
.card-header-violet {
  background: linear-gradient(135deg, oklch(0.96 0.025 300 / 0.65) 0%, transparent 100%);
}
```

**新增 `.orb-glow` 装饰光晕：** 用于卡片内部关键区域的柔和光晕。
```css
.orb-glow {
  position: relative;
}
.orb-glow::before {
  content: '';
  position: absolute;
  inset: -20% -10% auto auto;
  width: 160px;
  height: 160px;
  background: radial-gradient(circle, var(--orb-indigo), transparent 70%);
  filter: blur(20px);
  pointer-events: none;
  z-index: 0;
}
```

**字体层次优化：** 强化标题与正文的对比。
```css
body {
  font-family: "PingFang SC", "Hiragino Sans GB", system-ui, -apple-system, sans-serif;
  letter-spacing: -0.011em;
  font-feature-settings: "tnum", "ss01";
}
h1 { letter-spacing: -0.025em; font-weight: 700; }
h2 { letter-spacing: -0.02em; font-weight: 600; }
```

### 2. 布局层（`frontend/src/layouts/MainLayout.tsx`）

- **Sidebar**：背景改为 `bg-sidebar/70 backdrop-blur-xl`，移除 `border-r`，改为右侧 `border-r border-white/40`，底部增加一个 `.orb-glow` 装饰光晕（靛蓝）。
- **SidebarHeader Logo 区**：Logo 块增加 `backdrop-blur-sm` 和 `inset shadow`，让它在玻璃侧边栏上更立体。
- **Header**：从 `bg-card/50 backdrop-blur-sm` 升级为完整 `.glass-panel` 类（`bg-white/60 backdrop-blur-xl border-b border-white/40`），底部增加 1px 渐变分割线 `after:absolute after:bottom-0 after:left-0 after:h-px after:w-full after:bg-gradient-to-r after:from-transparent after:via-primary/20 after:to-transparent`。
- **Header 标题**：增加 `tracking-tight`，字号从 `text-sm` 提升到 `text-[15px]`，字重 `font-semibold`。
- **主内容区**：保持 `.main-canvas`，padding 从 `p-6` 调整为 `p-8`，给光晕更多呼吸空间。

### 3. 上传页（`frontend/src/pages/UploadPage.tsx`）

- **页面标题区**：在 `h1` 上方增加装饰元素——一个 `size-1.5 rounded-full bg-primary` 圆点 + `h-px w-8 bg-gradient-to-r from-primary/40 to-transparent` 细线，作为视觉锚点。
- **卡片**：保持 `.elevated-card`，移除 `border-0`（让玻璃边框显示）。
- **卡片头部**：从 `bg-gradient-to-r from-primary/5 to-accent/20` 改为 `.card-header-indigo`。
- **拖拽区**：
  - 默认态：`bg-white/40 backdrop-blur-sm border-white/60`，内部增加 `.orb-glow` 光晕。
  - Hover：`border-primary/50 bg-primary/[0.04]`。
  - 激活态：`border-primary bg-primary/10` + 已有 `upload-active`。
- **已选文件条**：从 `bg-muted/40` 改为 `bg-white/50 backdrop-blur-md border-white/50`（玻璃质感）。
- **图标块**（`size-10 rounded-xl bg-primary`）：增加 `inset shadow`：`shadow-[inset_0_1px_0_rgba(255,255,255,0.3)]`。

### 4. 历史记录页（`frontend/src/pages/HistoryPage.tsx`）

- **页面标题区**：同上传页增加圆点 + 细线装饰。
- **卡片头部**：改用 `.card-header-cyan`。
- **表格容器**：外层从 `border border-border/60` 改为 `bg-white/40 backdrop-blur-md border border-white/50`（玻璃包裹）。
- **表头**：从 `bg-muted/50` 改为 `bg-white/50 backdrop-blur-sm`，底部边框 `border-b border-white/40`。
- **表格行 hover**：从 `bg-muted/40` 改为 `bg-white/50`。
- **状态徽章**：
  - `done`：`bg-emerald-50/70 backdrop-blur-sm border border-emerald-200/50 text-emerald-700`。
  - `processing`：呼吸点外围增加 `drop-shadow-[0_0_4px_var(--primary)]` 光晕。
- **空状态**：圆形背景改为 `.orb-glow` 效果（柔和光晕替代纯色块），"去上传"按钮使用 `.btn-primary-gradient`。
- **分页按钮**：hover 时 `bg-white/60 backdrop-blur-sm border-white/60`。

### 5. 结果页（`frontend/src/pages/ResultPage.tsx`）

- **页面标题区**：增加圆点 + 细线装饰。
- **顶部信息卡片**：保持 `.elevated-card`。
- **总分区域**：从 `bg-gradient-to-br from-primary/10 to-accent/20` 升级为 `bg-white/50 backdrop-blur-md border border-white/50` + 内部 `.orb-glow`（靛蓝光晕在分数后方）。分数下方增加 `h-px w-12 bg-gradient-to-r from-primary/40 to-transparent mx-auto` 装饰线。
- **总体反馈卡片头部**：`.card-header-indigo`。
- **详细评分项卡片头部**：`.card-header-cyan`。
- **分数标签**：从 `bg-primary/10` 改为 `bg-gradient-to-br from-primary/15 to-accent/20 backdrop-blur-sm border border-white/40`。
- **OCR 卡片头部**：`.card-header-violet`。
- **OCR 折叠触发器**：从 `hover:bg-muted/50` 改为 `bg-white/40 backdrop-blur-sm hover:bg-white/60 border border-white/40`。
- **OCR 内容区**：从 `bg-muted/40` 改为 `bg-white/40 backdrop-blur-sm border border-white/30`。
- **处理中状态**：加载圈外围的 `animate-ping` 圆改为带光晕的 `drop-shadow-[0_0_8px_var(--primary)]`。

### 6. 设置页（`frontend/src/pages/SettingsPage.tsx`）

- **页面标题区**：增加圆点 + 细线装饰。
- **4 个卡片头部按顺序使用**：`.card-header-indigo` / `.card-header-cyan` / `.card-header-violet` / `.card-header-indigo`，形成节奏变化。
- **图标块**（`size-10 rounded-xl bg-primary`）：统一增加 `shadow-[inset_0_1px_0_rgba(255,255,255,0.3)]`。
- **输入框 focus 态**：在 `Input` 组件已有 ring 基础上，通过 index.css 全局增强：`input:focus, textarea:focus { box-shadow: 0 0 0 4px oklch(0.52 0.21 270 / 0.1); }`。
- **保存按钮区**：用 `.glass-panel` 包裹（`rounded-xl p-4 -mx-2`），按钮保持 `.btn-primary-gradient`，形成"玻璃托盘 + 渐变主按钮"的层次。
- **重置按钮**：增加 `hover:bg-white/60` 玻璃 hover 态。

### 7. 按钮 hover 通用增强（`frontend/src/index.css`）

为 `.btn-primary-gradient` 增加更精致的 hover 反馈：
```css
.btn-primary-gradient:hover {
  transform: translateY(-1px);
  box-shadow:
    0 8px 24px oklch(0.52 0.21 270 / 0.4),
    0 0 0 1px oklch(1 0 0 / 0.2) inset;
  filter: brightness(1.05);
}
```

## Assumptions & Decisions

- **美学方向**：柔光玻璃拟态风（用户已确认）——多层半透明白 + 柔和彩色光晕 + 毛玻璃叠加。
- **字体**：仅本地字体优化（用户已确认），不加载外部字体。通过字重（700/600/500）、字距（-0.025em/-0.02em/-0.011em）、字号层次提升高级感。
- **装饰强度**：中度层次（用户已确认）——全局背景 3 个光晕 blob，卡片玻璃化，关键区域局部光晕，但不加噪点纹理或渐变边框。
- **技术栈不变**：shadcn/ui + Tailwind v4，所有新增样式以 CSS 变量 + 自定义类实现，不引入依赖。
- **`backdrop-filter` 兼容性**：已加 `-webkit-backdrop-filter` 前缀，现代浏览器均支持。
- **性能**：`background-attachment: fixed` + 多层 `backdrop-blur` 可能在低端设备有性能开销，但 MVP 阶段可接受；如遇问题可降级为静态渐变。
- **不影响功能**：所有改动均为视觉层，不触碰表单逻辑、API 调用、路由、数据流。
- **暗色模式**：本次聚焦浅色模式玻璃效果，暗色模式变量保持不变（玻璃效果在暗色下需单独调优，暂不处理）。

## Verification Steps

1. 在 `/Users/mac/Desktop/AI-Marking/frontend` 运行 `npm run build`，确认无 TS 与 Vite 构建错误。
2. 运行 `npm run lint`，确认无 lint 报错。
3. 运行 `npm run dev`，手动检查：
   - **全局**：主内容区背景是否有 3 个柔和光晕，滚动时光晕固定不动（`background-attachment: fixed`）。
   - **侧边栏 + Header**：是否有玻璃质感，Header 底部是否有渐变分割线。
   - **上传页**：卡片头部靛蓝渐变，拖拽区玻璃感，已选文件条毛玻璃，按钮 hover 抬升。
   - **历史页**：卡片头部青色渐变，表格容器玻璃包裹，表头半透明，状态徽章玻璃感，空状态光晕。
   - **结果页**：总分区有光晕背景 + 装饰线，三个卡片头部颜色区分（靛蓝/青色/紫蓝），分数标签渐变玻璃。
   - **设置页**：4 个卡片头部渐变节奏变化，输入框 focus 有柔光环，保存区玻璃托盘。
4. **响应式检查**：窄屏下光晕不遮挡内容，玻璃面板不产生水平滚动条。
5. **性能抽查**：滚动时无明显卡顿（backdrop-blur 性能）。
