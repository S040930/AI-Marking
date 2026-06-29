# 修复计划：SettingsPage "重置为默认"按钮误提交表单

## Summary

[SettingsPage.tsx](file:///Users/mac/Desktop/AI-Marking/frontend/src/pages/SettingsPage.tsx) 中"评分标准(Rubric)"卡片头部的"重置为默认"按钮未设置 `type="button"`，默认作为 `type="submit"` 存在，点击会触发表单提交。本计划仅修复该关键 BUG，其他体验优化项不在本次范围内。

## Current State Analysis

- 文件：[frontend/src/pages/SettingsPage.tsx](file:///Users/mac/Desktop/AI-Marking/frontend/src/pages/SettingsPage.tsx)
- 问题位置：第 268-276 行，`CardAction` 内的 `Button` 组件
- 当前代码：
  ```tsx
  <CardAction>
    <Button
      variant="ghost"
      size="sm"
      onClick={handleResetRubric}
    >
      <Undo2 />
      重置为默认
    </Button>
  </CardAction>
  ```
- 根因：HTML `button` 默认类型为 `submit`，该按钮位于 `<form>` 内部（通过 `Form` 组件），点击后会触发表单 `onSubmit`，导致非预期的配置保存请求。

## Proposed Changes

### 1. 修复重置按钮类型

- 文件：[frontend/src/pages/SettingsPage.tsx](file:///Users/mac/Desktop/AI-Marking/frontend/src/pages/SettingsPage.tsx)
- 修改内容：为"重置为默认"按钮显式添加 `type="button"`
- 修改后代码：
  ```tsx
  <CardAction>
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={handleResetRubric}
    >
      <Undo2 />
      重置为默认
    </Button>
  </CardAction>
  ```
- 为什么：阻止按钮触发表单提交，仅执行 `handleResetRubric` 清空 rubric 字段并提示用户。

## Assumptions & Decisions

- 范围限定为仅修复该关键 BUG，不涉及主题切换、密码可见性、tooltip、页面标题等其他优化项。
- 不引入新依赖。
- 保持现有 UI 样式不变（`variant="ghost" size="sm"` 不变）。

## Verification Steps

1. 修改后运行 `npm run build`，确认 TypeScript 编译无错误。
2. 运行 `npm run lint`，确认无 lint 错误。
3. 本地启动前端（`npm run dev`），进入设置页：
   - 在 Rubric 文本框中输入任意内容。
   - 点击"重置为默认"按钮。
   - 预期结果：Rubric 字段被清空，弹出 "Rubric 已清空,保存后将使用默认 rubric" 提示，**不应触发保存配置请求或显示"配置已保存"提示**。
