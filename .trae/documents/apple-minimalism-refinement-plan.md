# UI 极简化改造计划：回归苹果极简风

## 摘要

当前界面采用"柔光玻璃拟态风"，包含大量装饰元素（3 层光晕背景、backdrop-filter 玻璃面板、三色卡片头部渐变、orb-glow 装饰光晕、图标 inset 高光、按钮渐变提升、呼吸点 drop-shadow 等）。用户反馈该方向过度装饰，要求回归苹果式极简风：朴素、克制、留白、细腻层次。

本计划将系统性移除所有过度装饰，回归苹果设计语言的核心：**纯色背景、单一强调色、极轻阴影、字重与间距建立层次、保留语义色**。仅调整视觉，不改动任何功能逻辑。

---

## 一、当前状态分析

### 1.1 过度装饰清单（需移除/简化）

| 装饰类型 | 位置 | 问题 |
|---------|------|------|
| 3 层 radial-gradient 光晕背景 | `index.css` `.main-canvas` | 过度图案 |
| backdrop-filter 玻璃面板 | `index.css` `.glass-panel`/`.elevated-card` + 各页面 | 过度质感 |
| 三色卡片头部渐变 | `index.css` `.card-header-indigo\|cyan\|violet` | 过度色彩 |
| orb-glow 装饰光晕伪元素 | `index.css` `.orb-glow::before` + MainLayout/Upload/History/Result | 过度图案 |
| 标题区圆点+渐变细线 | 4 个页面顶部 | 过度装饰 |
| 图标块 inset 高光 | Upload/Settings 图标块 `[box-shadow:inset_0_1px_0_rgba(255,255,255,0.3),...]` | 过度质感 |
| 按钮渐变+提升阴影 | `index.css` `.btn-primary-gradient` + 各页面按钮 | 过度鲜艳 |
| 呼吸点 drop-shadow 光晕 | HistoryPage 状态徽章 `[filter:drop-shadow(0_0_4px_var(--primary))]` | 过度光晕 |
| 处理中 drop-shadow 光晕 | ResultPage `[filter:drop-shadow(0_0_8px_var(--primary))]` | 过度光晕 |
| 半透明背景 + backdrop-blur | 各页面卡片/表格/徽章 `bg-white/40 backdrop-blur-*` | 过度质感 |
| 上传区脉冲动画 | `index.css` `.upload-active` | 过度动效（可保留极简版） |
| 输入框 focus 柔光环 | `index.css` `input:focus` 4px box-shadow | 可简化为 ring |

### 1.2 需保留的元素

- 字体优化：PingFang SC + 字重/字距层次（h1: -0.025em/700, h2: -0.02em/600, body: -0.011em）
- oklch 色彩变量体系（去除装饰变量，保留主题变量）
- 语义色：emerald（已完成）、destructive（失败）、primary（处理中/激活）
- shadcn/ui 组件原生结构
- 所有功能逻辑、API、路由

---

## 二、设计原则（苹果极简风）

1. **纯色背景**：去除所有 radial-gradient 光晕，使用纯 `--background` 色
2. **单一强调色**：仅 `--primary`（靛蓝）用于按钮、链接、激活态；去除 indigo/cyan/violet 三色区分
3. **极轻阴影**：卡片仅用 `shadow-sm` 或无边框仅靠极淡 border 区分
4. **留白建立层次**：通过 padding/margin/gap 而非颜色建立视觉层次
5. **字重层次**：h1 700、h2 600、body 400，通过字重而非颜色区分
6. **语义色保留**：成功（emerald）、失败（destructive）、处理中（primary）保留，但去除 backdrop-blur
7. **无装饰图案**：移除所有圆点+细线、orb-glow、渐变光晕
8. **极简边框**：使用 `border-border`（oklch 0.9 0.01 260）而非 `border-white/40`

---

## 三、具体改动

### 3.1 `frontend/src/index.css`

**移除：**
- 第 41-48 行：玻璃拟态装饰变量（`--glass-bg`, `--glass-border`, `--glass-shadow`, `--orb-indigo`, `--orb-cyan`, `--orb-violet`, `--orb-warm`）
- 第 168-176 行：`.main-canvas` 的 radial-gradient 光晕背景
- 第 178-188 行：`.glass-panel` 工具类
- 第 190-212 行：`.elevated-card` 的 backdrop-filter 玻璃质感（改为极简卡片）
- 第 214-225 行：`.card-header-indigo|cyan|violet` 三色渐变
- 第 227-242 行：`.orb-glow` 装饰光晕
- 第 244-248 行：输入框 focus 4px 柔光环（交还给 shadcn 默认 ring）
- 第 250-270 行：`.btn-primary-gradient` 渐变按钮（改为纯色）
- 第 272-285 行：`.upload-active` 脉冲动画（改为极简边框高亮即可，由 Tailwind 类实现）

**保留：**
- 第 1-49 行 `:root` 主题变量（去除装饰变量后）
- 第 51-84 行 `.dark` 暗色主题
- 第 86-123 行 `@theme inline`
- 第 125-132 行 `@layer base`
- 第 134-166 行 字体与字重优化

**新增/改写：**

```css
/* 极简卡片：极轻阴影 + 极淡边框 */
.elevated-card {
  background: var(--card);
  border: 1px solid var(--border);
  box-shadow: 0 1px 2px 0 oklch(0.2 0.02 270 / 0.04);
  transition: box-shadow 200ms ease, border-color 200ms ease;
}

.elevated-card:hover {
  box-shadow: 0 2px 8px 0 oklch(0.2 0.02 270 / 0.06);
  border-color: oklch(0.88 0.01 260);
}
```

（不再需要 `.main-canvas`、`.glass-panel`、`.card-header-*`、`.orb-glow`、`.btn-primary-gradient`、`.upload-active` 这些类，从 CSS 中删除。各页面引用处一并清理。）

### 3.2 `frontend/src/layouts/MainLayout.tsx`

**Sidebar（第 35 行）：**
- 移除：`orb-glow border-r border-white/40 bg-sidebar/70 backdrop-blur-xl`
- 改为：`border-r bg-sidebar`

**Logo 块（第 38 行）：**
- 移除：`shadow-md shadow-primary/25 backdrop-blur-sm [box-shadow:inset_0_1px_0_rgba(255,255,255,0.3),0_4px_12px_oklch(0.52_0.21_270/0.25)]`
- 改为：`bg-primary text-primary-foreground`

**导航激活态图标（第 69 行）：**
- 移除：`shadow-sm [box-shadow:inset_0_1px_0_rgba(255,255,255,0.3)]`
- 保留：`bg-primary text-primary-foreground`

**Header（第 93 行）：**
- 移除：`glass-panel relative ... after:absolute after:bottom-0 after:left-0 after:h-px after:w-full after:bg-gradient-to-r after:from-transparent after:via-primary/20 after:to-transparent`
- 改为：`border-b bg-background`

**主内容区（第 100 行）：**
- 移除：`main-canvas`
- 改为：`bg-background`
- padding `p-8` 保留（苹果式留白）

### 3.3 `frontend/src/pages/UploadPage.tsx`

**标题区（第 52-55 行）：**
- 移除整个 `<div className="mb-3 flex items-center gap-2">` 圆点+细线装饰

**卡片头部（第 65 行）：**
- 移除：`card-header-indigo`
- CardHeader 仅保留 `pb-6`

**图标块（第 67 行）：**
- 移除：`shadow-lg shadow-primary/25 [box-shadow:inset_0_1px_0_rgba(255,255,255,0.3),0_8px_20px_oklch(0.52_0.21_270/0.3)]`
- 改为：`bg-primary text-primary-foreground`

**拖拽区（第 82-86 行）：**
- 移除：`orb-glow ... border-white/60 bg-white/40 backdrop-blur-sm hover:border-primary/50 hover:bg-primary/[0.04]`
- 改为：`border-border bg-muted/40 hover:border-primary/40 hover:bg-muted/60`
- 拖拽激活态：移除 `upload-active`，保留 `border-primary bg-primary/5`

**文件条（第 110 行）：**
- 移除：`border-white/50 bg-white/50 backdrop-blur-md`
- 改为：`border-border bg-muted/50`

**提交按钮（第 138 行）：**
- 移除：`btn-primary-gradient`
- 保留默认 shadcn Button 样式

### 3.4 `frontend/src/pages/HistoryPage.tsx`

**标题区（第 95-98 行）：**
- 移除圆点+细线装饰

**卡片头部（第 114 行）：**
- 移除：`card-header-cyan`

**表格容器（第 118 行）：**
- 移除：`border-white/50 bg-white/40 backdrop-blur-md`
- 改为：`border-border`

**表头（第 120 行）：**
- 移除：`border-white/40 bg-white/50 backdrop-blur-sm`
- 改为：`bg-muted/50`

**状态徽章（第 49 行 done）：**
- 移除：`backdrop-blur-sm`
- 改为：`border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100`

**呼吸点（第 63 行）：**
- 移除：`[filter:drop-shadow(0_0_4px_var(--primary))]`
- 保留：`animate-ping` + `bg-primary`

**空状态（第 141 行）：**
- 移除：`orb-glow`

**空状态按钮（第 148 行）：**
- 移除：`btn-primary-gradient`

**行 hover（第 160 行）：**
- 改为：`hover:bg-muted/40`

**完成徽章在 ResultPage 第 146 行同理**（见 3.5）

### 3.5 `frontend/src/pages/ResultPage.tsx`

**标题区（第 133-136 行）：**
- 移除圆点+细线装饰

**完成徽章（第 144-150 行）：**
- 移除：`backdrop-blur-sm`
- 改为：`border-emerald-200 bg-emerald-50 text-emerald-700`

**总分区（第 173 行）：**
- 移除：`orb-glow ... border-white/50 bg-white/50 backdrop-blur-md`
- 改为：`border-border bg-muted/50`
- 移除（第 180 行）：`bg-gradient-to-r from-primary/40 to-transparent` 装饰线

**三个卡片头部：**
- 第 186 行：移除 `card-header-indigo`
- 第 203 行：移除 `card-header-cyan`
- 第 233 行：移除 `card-header-violet`

**反馈区（第 191 行）：**
- 移除：`border-white/30 bg-white/40 backdrop-blur-sm`
- 改为：`border-border bg-muted/50`

**分数标签（第 216 行）：**
- 移除：`border-white/40 bg-gradient-to-br from-primary/15 to-accent/20 backdrop-blur-sm`
- 改为：`bg-primary/10 text-primary`

**OCR 折叠触发器（第 239 行）：**
- 移除：`border-white/40 bg-white/40 backdrop-blur-sm hover:bg-white/60`
- 改为：`border-border bg-muted/50 hover:bg-muted`

**OCR 内容区（第 244 行）：**
- 移除：`border-white/30 bg-white/40 backdrop-blur-sm`
- 改为：`border-border bg-muted/50`

**处理中状态（第 89 行）：**
- 移除：`[filter:drop-shadow(0_0_8px_var(--primary))]`
- 保留：`bg-primary/10` + `animate-ping`

**失败按钮（第 121 行）：**
- 移除：`btn-primary-gradient`

### 3.6 `frontend/src/pages/SettingsPage.tsx`

**标题区（第 143-146 行）：**
- 移除圆点+细线装饰

**4 个卡片头部：**
- 第 161 行：移除 `card-header-indigo`
- 第 231 行：移除 `card-header-cyan`
- 第 289 行：移除 `card-header-violet`
- 第 339 行：移除 `card-header-indigo`

**4 个图标块（第 163/233/292/341 行）：**
- 移除：`shadow-lg shadow-primary/25 [box-shadow:inset_0_1px_0_rgba(255,255,255,0.3),0_8px_20px_oklch(0.52_0.21_270/0.3)]`
- 改为：`bg-primary text-primary-foreground`

**重置按钮（第 307 行）：**
- 移除：`hover:bg-white/60`
- 保留默认 ghost hover

**保存区（第 382 行）：**
- 移除：`glass-panel`
- 改为：`bg-muted/50 border-border`

**保存按钮（第 387 行）：**
- 移除：`btn-primary-gradient`

---

## 四、假设与决策

1. **保留单一强调色**：`--primary`（靛蓝 oklch 0.52 0.21 270）用于按钮、链接、激活态、分数高亮。这是苹果式克制——单一系统蓝。
2. **保留语义色**：emerald（已完成）、destructive（失败）、primary（处理中）。这是功能性而非装饰性，苹果设计同样保留。
3. **保留字体优化**：PingFang SC + 字重/字距层次。这是苹果式排版的核心。
4. **保留留白**：`p-8` 主内容区 padding 保留，体现苹果式呼吸感。
5. **保留卡片极轻阴影**：`0 1px 2px 0 oklch(0.2 0.02 270 / 0.04)`——几乎不可见但提供层次。这是苹果式细腻。
6. **保留 `animate-ping`**：处理中状态的呼吸点保留动画，但去除 drop-shadow 光晕。动画是功能性的（指示进行中），光晕是装饰性的。
7. **不改动功能**：所有 API、路由、状态管理、表单逻辑保持不变。
8. **不新增文件**：仅修改现有 6 个文件。

---

## 五、验证步骤

1. **构建验证**：`cd frontend && npm run build` 确认无构建错误
2. **Lint 验证**：`cd frontend && npm run lint` 确认 0 错误
3. **视觉验证**：`npm run dev` 启动开发服务器，逐页检查：
   - 上传页：拖拽区朴素、无光晕、无玻璃质感
   - 历史页：表格纯色背景、状态徽章无 backdrop-blur、呼吸点无光晕
   - 结果页：总分卡朴素、三个卡片头部无渐变区分、OCR 区朴素
   - 设置页：4 个卡片头部统一无渐变、图标块无 inset 高光
   - 全局：Sidebar/Header 纯色无玻璃、无光晕背景
4. **残留检查**：全局搜索确认无残留装饰类引用：
   - `orb-glow`、`glass-panel`、`main-canvas`、`card-header-`、`btn-primary-gradient`、`upload-active`、`backdrop-blur`、`drop-shadow(0_0`、`inset_0_1px_0_rgba(255,255,255`、`from-primary/40 to-transparent`（标题细线）、`size-1.5 rounded-full bg-primary`（标题圆点）

---

## 六、执行顺序

1. 先改 `index.css`（移除装饰变量与工具类，改写 `.elevated-card`）
2. 再改 `MainLayout.tsx`（全局布局）
3. 并行改 4 个页面（UploadPage、HistoryPage、ResultPage、SettingsPage）
4. 运行 build + lint 验证
5. 全局搜索残留装饰类清理
