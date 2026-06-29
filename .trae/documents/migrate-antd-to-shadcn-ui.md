# 迁移方案：前端从 Ant Design 切换到 shadcn/ui

## Summary

将 `/Users/mac/Desktop/AI-Marking/frontend` 前端组件库从 **antd v6 + @ant-design/icons** 整体迁移到 **shadcn/ui**（基于 Tailwind CSS v4 + Radix UI）。采用 shadcn 默认浅色风格（neutral 基色），表单使用 `react-hook-form + zod` 的标准 shadcn 模式。迁移范围限定在 5 个使用 antd 的文件 + 基础设施配置，API 层与路由层不动。

迁移为一次性全量切换（项目仅 5 个 antd 文件），采用"基础设施先行 → 逐页迁移 → 移除 antd"的顺序，迁移期间 antd 与 shadcn 可短暂共存（antd v6 为 CSS-in-JS，与 Tailwind 无冲突）。

---

## Current State Analysis（基于实际探索）

### 技术现状
- **antd v6.4.5 + @ant-design/icons v6.2.5**，无 `ConfigProvider`、无主题配置、无 locale
- **无 Tailwind / 无 CSS Modules / 无 styled-components**，样式全靠内联 `style` 属性
- `src/index.css` 仅 11 行基础 reset，**无任何 antd 覆盖**
- 路径别名 `@/*` → `./src/*` 已在 `vite.config.ts` 与 `tsconfig.app.json` 配置好
- React 19 + Vite 8 + TS 6 + React Router v7 + TanStack Query v5 + axios + dayjs

### antd 使用面（共 5 个文件）
| 文件 | antd 组件 | 迁移难点 |
|---|---|---|
| `src/layouts/MainLayout.tsx` | Layout/Header/Sider/Content/Menu + 3 图标 | shadcn Sidebar block 重写布局 |
| `src/pages/UploadPage.tsx` | Card/Upload.Dragger/Button/message/Typography + InboxOutlined | react-dropzone 自建拖拽区 + sonner |
| `src/pages/HistoryPage.tsx` | Card/Table/Tag/Button/Space/Typography（`ColumnsType` 深路径导入） | shadcn Table + 手动分页 + Badge |
| `src/pages/ResultPage.tsx` | Card/Spin/Result/Button/Statistic/Collapse/Descriptions/List/Typography/Space/Empty | 多个展示组件无直接对应，需自定义组合 |
| `src/pages/SettingsPage.tsx` | Card/Form/Form.Item/Form.useForm/Input/TextArea/Button/message/Spin/Typography/Space/Divider + 2 图标 | react-hook-form + zod 重写表单（工作量最大） |

### 命令式 `message` API（6 处，2 文件）
- `UploadPage.tsx` L17/28/32：error / success / error
- `SettingsPage.tsx` L52/55/62：success / error / info
- → 全部替换为 `sonner` 的 `toast.success/error/info`

### 已与 antd 解耦（不动）
- `src/main.tsx`、`src/App.tsx`（仅加 Toaster）、`src/router/index.tsx`
- `src/api/*.ts`（纯数据层，types 不变）
- 后端完全不动

---

## 组件映射表

| antd | shadcn / 替代方案 |
|---|---|
| Button | `button`（variants: default/secondary/link/outline/ghost） |
| Card + title | `card`（CardHeader/CardTitle/CardDescription/CardContent） |
| Typography (Title/Text/Paragraph) | 原生 `<h1>`-`<h6>`/`<p>`/`<span>` + Tailwind 类 |
| Space | `flex gap-*` 工具类 |
| Spin | `lucide-react` `Loader2` + `animate-spin`（内联，不建文件） |
| Empty | 内联空态（`ResultPage` 内定义局部 helper，不新建文件） |
| message | `sonner` `toast` + `<Toaster />` |
| Layout/Header/Sider/Content + Menu | shadcn `sidebar` block（SidebarProvider/Sidebar/SidebarContent/SidebarMenu/SidebarMenuButton/SidebarInset） |
| Upload.Dragger | `react-dropzone` + 自定义拖拽 UI |
| Table + ColumnsType | shadcn `table` 原语 + 手动 `useState` 分页（不引入 @tanstack/react-table） |
| Tag | `badge`（variants） |
| Form/Form.Item/Form.useForm | shadcn `form`（react-hook-form + zod） |
| Input/Input.Password | `input`（type="password"） |
| TextArea | `textarea` |
| Divider | `separator` |
| Result (status="error") | `alert`（destructive variant）+ 居中布局 |
| Statistic | 自定义 `<div>`（label + 大号数字） |
| Collapse | `accordion` |
| Descriptions | 自定义 `<dl>`/`<dt>`/`<dd>` 或 labeled grid |
| List/List.Item | `div` + border 工具类 |
| UploadOutlined/HistoryOutlined/SettingOutlined/InboxOutlined/SaveOutlined/UndoOutlined | `lucide-react`: Upload/History/Settings/Inbox/Save/Undo2 |

### 决策：Table 不使用 @tanstack/react-table
HistoryPage 是 5 列静态小表（每页 10 条），引入 @tanstack/react-table 属过度工程。改用 shadcn `table` 原语 + `useState` 手动 `slice()` 分页 + Loading 时用 Skeleton/Spinner。仍属 shadcn 体系（用 shadcn 的 Table 组件），仅不套 DataTable wrapper。

---

## Proposed Changes

### 阶段 0：基础设施搭建

#### 0.1 安装依赖
```bash
# Tailwind v4 + Vite 插件（dev）
npm install -D tailwindcss @tailwindcss/vite

# shadcn 运行时依赖
npm install class-variance-authority clsx tailwind-merge lucide-react

# 表单
npm install react-hook-form @hookform/resolvers zod

# Toast
npm install sonner

# 上传拖拽
npm install react-dropzone
```

> Radix UI 依赖（@radix-ui/react-slot、react-label、accordion、separator 等）由 `npx shadcn@latest add` 命令在添加各组件时自动安装，无需手动列出。

#### 0.2 修改 `vite.config.ts`
新增 `@tailwindcss/vite` 插件：
```ts
import tailwindcss from '@tailwindcss/vite'
// ...
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // resolve.alias / server.proxy 保持不变
})
```

#### 0.3 替换 `src/index.css`
运行 `npx shadcn@latest init`（选择 `neutral` 基色、CSS variables 开启），由 CLI 生成 Tailwind v4 的 `@import "tailwindcss"` + `:root`/`.dark` CSS 变量 + base layer。保留原有 `html/body/#root { margin:0; padding:0; height:100% }` 与 body 字体设置（合并到 base layer）。

预期产物包含：
- `@import "tailwindcss";`
- `:root { --background: ...; --foreground: ...; --primary: ...; ... }`（neutral 浅色）
- `.dark { ... }` 变体
- `@layer base { * { @apply border-border; } body { @apply bg-background text-foreground; } }`
- 追加：`html, body, #root { margin:0; padding:0; height:100%; }` + body `font-family`

#### 0.4 新建 `src/lib/utils.ts`
```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

#### 0.5 新建 `components.json`（shadcn 配置，CLI 生成/确认）
关键字段：
- `style`: `"new-york"`（shadcn 默认现代风格）
- `rsc`: `false`（非 Next.js，Vite 项目）
- `tsx`: `true`
- `tailwind.config`: `""`（v4 CSS-first，无 config 文件）
- `aliases.components`: `@/components`
- `aliases.ui`: `@/components/ui`
- `aliases.lib`: `@/lib`
- `aliases.utils`: `@/lib/utils`

#### 0.6 tsconfig 调整
为满足 shadcn CLI 对路径解析的要求，在 `tsconfig.json` 根级补 `compilerOptions.baseUrl: "."` 与 `paths: { "@/*": ["./src/*"] }`（与 `tsconfig.app.json` 一致），避免 CLI 报错。`tsconfig.app.json` 已有 paths，保持不变。

#### 0.7 通过 CLI 生成 shadcn 组件
```bash
npx shadcn@latest add button input label textarea card sonner form table accordion alert badge separator skeleton scroll-area sidebar tooltip
```
生成到 `src/components/ui/`，每个组件一个文件。`sidebar` block 会附带 `use-mobile` hook 与 `sheet`/`tooltip` 依赖。

> 若 CLI 因 Vite 框架检测或网络失败，回退为从 shadcn 官方仓库手动复制对应组件源码到 `src/components/ui/`，内容以 shadcn 官方 v4 版本为准。

---

### 阶段 1：App.tsx 接入 Toaster

修改 `src/App.tsx`，在 `RouterProvider` 后挂载 sonner Toaster（antd `message` 此期间仍可用，迁移期共存）：
```tsx
import { Toaster } from '@/components/ui/sonner';
// ...
<QueryClientProvider client={queryClient}>
  <RouterProvider router={router} />
  <Toaster richColors position="top-center" />
</QueryClientProvider>
```

---

### 阶段 2：逐页迁移（按复杂度从低到高）

#### 2.1 `src/pages/UploadPage.tsx`
- 移除 antd `Card/Upload/Button/message/Typography` 与 `InboxOutlined`
- 用 `react-dropzone` 的 `useDropzone`：`accept: { 'application/pdf': ['.pdf'] }`、`maxFiles: 1`、`onDrop` 校验 `.pdf` 后缀（不符则 `toast.error('仅支持 PDF 文件')` 并拒绝）
- 拖拽区 UI：dashed border + `Inbox` 图标（lucide）+ 提示文字，选中后显示文件名
- `Button`（shadcn）触发 `uploadMutation.mutate`，`disabled={!file}`，pending 时显示 `Loader2` spin
- 成功/失败 → `toast.success`/`toast.error`
- 外层用 `Card`（shadcn）

#### 2.2 `src/pages/HistoryPage.tsx`
- 移除 antd `Card/Table/Tag/Button/Space/Typography` 与 `ColumnsType` 深路径导入
- 数据：`useSubmissions()` 不变
- 表格：shadcn `Table`（TableHeader/TableBody/TableRow/TableCell/TableHead）+ 手动分页
  - `const [page, setPage] = useState(1)`；`pageSize = 10`
  - `paged = (data ?? []).slice((page-1)*pageSize, page*pageSize)`
  - 分页控件：上一页/下一页 `Button`（variant outline）+ 页码文字
- 状态列：`Badge`（variant 映射：done→default, failed→destructive, processing→secondary, 其他→outline）+ 中文文案
- 分数列：`null` 显示 `-`（muted 色）
- 时间列：`dayjs(...).format('YYYY-MM-DD HH:mm:ss')`（dayjs 保留）
- 操作列：`Button` variant="link" → `navigate(/result/:id)`
- Loading：`Skeleton` 行 × 5
- 空数据：内联空态（图标 + "暂无数据"）
- 外层 `Card`

#### 2.3 `src/pages/ResultPage.tsx`
- 移除 antd 9 个组件导入
- Loading 态：居中 `Loader2` `animate-spin`
- 未找到：内联空态 "未找到记录"
- 处理中：居中 `Loader2` + 状态/时间文字（`<p className="text-muted-foreground">`）
- 失败态：`Alert`（variant="destructive"）标题 "批改失败" + `error_message` + `Button`（返回上传），居中布局
- 完成态：
  - 顶部 `Card`：左侧文件名（`<h4>`）+ 上传/完成时间（muted），右侧自定义 Statistic（`<p className="text-sm text-muted-foreground">总分</p>` + `<p className="text-3xl font-bold text-green-600">{score}</p>`）
  - 总体反馈 `Card`：`<p className="whitespace-pre-wrap">` 或内联空态
  - 详细评分项 `Card`：`data.details.map(...)` → 每项一个 `div`（border-b），含 criterion 标题、得分、评语（用 `<dl>`/`<dt>`/`<dd>` 或 labeled grid）
  - OCR 原文 `Card`：`Accordion`（type="single"）含一项，展开显示 `ocr_text`（`whitespace-pre-wrap`）
- `dayjs` 保留用于时间格式化

#### 2.4 `src/pages/SettingsPage.tsx`（工作量最大）
- 移除 antd 11 个组件导入与 2 个图标
- 新增 zod schema（字段全 optional，无校验规则，对应 `ConfigUpdate`）：
  ```ts
  import { z } from 'zod';
  const configSchema = z.object({
    llm_api_key: z.string().optional(),
    llm_base_url: z.string().optional(),
    llm_model: z.string().optional(),
    paddleocr_api_url: z.string().optional(),
    paddleocr_token: z.string().optional(),
    rubric: z.string().optional(),
  });
  type ConfigFormValues = z.infer<typeof configSchema>;
  ```
- `useForm<ConfigFormValues>({ resolver: zodResolver(configSchema), defaultValues: {...} })`
- 数据回填：`useEffect` 中 `form.reset({ ...data })`（替代 antd `form.setFieldsValue`），依赖 `[data]`
- 提交：`form.handleSubmit((values) => updateMutation.mutate(values, { onSuccess: () => toast.success('配置已保存'), onError: (e) => toast.error(e.message || '保存失败') }))`
- 重置 rubric：`form.setValue('rubric', '', { shouldDirty: true })` + `toast.info('Rubric 已清空,保存后将使用默认 rubric')`
- Loading 态：居中 `Loader2`
- 错误态：`Alert` destructive 显示 `error.message`
- 表单 UI：shadcn `Form`/`FormField`/`FormItem`/`FormLabel`/`FormControl`/`FormMessage` + `Input`/`Input[type=password]`/`Textarea`
- 三个分组用 `Card`（LLM 配置 / PaddleOCR 配置 / Rubric），每组 `FormLabel` + tooltip 用原生 `<span>` + title 或简短说明文字（shadcn 无 Form.Item tooltip，改用 `FormDescription`）
- Rubric 卡片右上角"重置为默认"用 `Button` variant="ghost" size="sm" + `Undo2` 图标
- 底部"保存配置" `Button`（default variant, size lg）+ `Save` 图标，pending 时 `Loader2` spin + disabled
- 说明性段落用 `CardDescription` 或 `<p className="text-sm text-muted-foreground">`

#### 2.5 `src/layouts/MainLayout.tsx`
- 移除 antd `Layout/Menu` 与 3 个图标
- 用 shadcn `sidebar` block：
  - `SidebarProvider` 包裹
  - `Sidebar`：`SidebarHeader`（标题 "AI 作业批改系统"）、`SidebarContent` 含 `SidebarMenu`/`SidebarMenuItem`/`SidebarMenuButton`
  - 3 个菜单项，用 react-router `NavLink` 包裹，`isActive` 基于 `NavLink` 的 isActive 或 `useLocation().pathname` 计算（保留原 `selectedKey` 逻辑：/history→history, /settings→settings, / 与 /result→upload）
  - 图标：lucide `Upload`/`History`/`Settings`
  - `SidebarInset`：含一个顶部 `<header>`（标题或留空）+ `<main className="p-6 overflow-auto">` 渲染 `<Outlet />`
- 移除原深色侧栏 `#001529` 内联样式，采用 shadcn 默认浅色 sidebar 样式

---

### 阶段 3：移除 antd

确认所有 5 个文件不再 import antd 后：
```bash
npm uninstall antd @ant-design/icons
```

检查 `src/` 全局无 `from 'antd'` / `from '@ant-design/icons'` 残留（Grep 验证）。

---

## Assumptions & Decisions

| 项 | 决策 | 理由 |
|---|---|---|
| Tailwind 版本 | v4 + `@tailwindcss/vite` | 2026 年标准，shadcn 官方支持，CSS-first 配置 |
| shadcn 风格 | new-york + neutral 基色 + 浅色 | 用户选择"shadcn 默认" |
| 表单方案 | react-hook-form + zod | 用户选择，shadcn 标准模式 |
| 视觉风格 | shadcn 默认浅色（放弃深色侧栏） | 用户选择"采用 shadcn 默认" |
| Table 方案 | shadcn Table 原语 + 手动分页（不用 @tanstack/react-table） | 5 列静态小表，避免过度工程，符合 MVP 简单优先 |
| Upload 方案 | react-dropzone + 自定义 UI | shadcn 无 Upload 组件，dropzone 是事实标准 |
| Toast | sonner | shadcn 官方推荐 |
| 空态/Spinner | 内联（不新建组件文件） | 遵循"避免不必要文件"，Spinner 仅 `Loader2`+`animate-spin` |
| 路径别名 | 复用现有 `@/*` | 已配置好，shadcn CLI 兼容 |
| 迁移策略 | 一次性全量、逐页推进 | 仅 5 文件，共存期 antd CSS-in-JS 与 Tailwind 无冲突 |
| 后端 | 完全不动 | 仅前端组件库切换 |
| API 层 types | 不动 | `ConfigUpdate`/`SubmissionOut` 等类型与 UI 库无关 |

---

## Verification

1. **类型检查 + 构建**：`cd frontend && npm run build`（执行 `tsc -b && vite build`，须无 TS 错误、构建成功）
2. **Lint**：`npm run lint`（oxlint，须无错误）
3. **残留检查**：Grep `from 'antd'` 与 `from '@ant-design/icons'` 在 `src/` 下须 0 匹配
4. **依赖检查**：`npm ls antd @ant-design/icons` 须显示 "(empty)" 或 not found
5. **手动冒烟**（`npm run dev`）：
   - 访问 `/` 上传页：拖拽 PDF 高亮、非 PDF 提示 toast、选中后"开始批改"按钮启用
   - 访问 `/settings`：配置回填、修改后保存 toast、重置 rubric toast
   - 访问 `/history`：列表渲染、分页翻页、状态 Badge 颜色、点击"查看"跳转
   - 访问 `/result/:id`：处理中 spinner、失败 Alert、完成态 Statistic + Accordion 展开 OCR
   - 侧栏：3 菜单项激活态正确、点击导航正常
6. **视觉**：整体浅色 neutral 风格，无 antd 残留样式
