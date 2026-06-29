# 修正方案：仅保留"用户提示词"可自定义，移除"系统提示词"可配置

## Summary

回滚上一轮过度添加的 `llm_system_prompt` 可配置项。最终状态：**系统提示词（SYSTEM_PROMPT）保持硬编码不可改**，仅"用户提示词模板（llm_user_prompt）"可在设置页自定义。这是对上一轮"系统提示词+用户提示词都可自定义"实现的精简修正。

## Current State Analysis（基于实际探索）

上一轮已在 7 个文件中加入 `llm_system_prompt` + `llm_user_prompt` 两个新字段：

| 文件 | 当前状态 | 需要动作 |
|---|---|---|
| `backend/app/core/prompt.py` | `build_user_prompt` 接受 `user_prompt_template` 参数（L45-64） | **保留**（此参数是用户提示词的，正确） |
| `backend/app/schemas/system_config.py` | `ConfigUpdate` 与 `ConfigOut` 各含 `llm_system_prompt` + `llm_user_prompt`（L34-40, L54-55） | **删除 `llm_system_prompt`**，保留 `llm_user_prompt` |
| `backend/app/services/config.py` | `CONFIG_KEYS` 白名单含两个 key（L23-24） | **删除 `llm_system_prompt`** 行 |
| `backend/app/api/config.py` | `_to_config_out` 映射两字段（L26-27） | **删除 `llm_system_prompt`** 行 |
| `backend/app/services/llm.py` | L66 读取 `system_prompt`，L80 `system_prompt or SYSTEM_PROMPT` | **删除 L66 读取**，L80 改回 `SYSTEM_PROMPT` |
| `backend/tests/test_config_api.py` | L15 断言 `llm_system_prompt == ""` | **删除该断言** |
| `frontend/src/api/config.ts` | `ConfigOut`/`ConfigUpdate` 含两字段（L12, L26） | **删除 `llm_system_prompt`** |
| `frontend/src/pages/SettingsPage.tsx` | zod schema + defaultValues + reset + 一个 Card（L307-337） | **删除 schema/defaultValues/reset 中的 `llm_system_prompt`**，**删除整个"LLM 系统提示词"Card** |

关键点：
- `SYSTEM_PROMPT` 常量在 [prompt.py:8-9](file:///Users/mac/Desktop/AI-Marking/backend/app/core/prompt.py#L8-L9) 已存在，llm.py L16 已 import，无需改动 import
- `build_user_prompt` 函数签名不动（`user_prompt_template` 参数是正确的）
- 数据库 `system_config` 表是 key-value 结构，不需要 Alembic 迁移；若用户之前已保存过 `llm_system_prompt` key，会变成"未声明的孤儿 key"——但因为 `_to_config_out` 不再读它、API 也不再接受它（Pydantic `extra="forbid"`），它只是静静留在 DB 里不影响功能。不主动清理（避免引入迁移脚本，符合 MVP 简单优先）

## Proposed Changes

### 1. `backend/app/schemas/system_config.py`
删除 `ConfigUpdate.llm_system_prompt`（L34-36）与 `ConfigOut.llm_system_prompt`（L54）字段。保留 `llm_user_prompt`。

### 2. `backend/app/services/config.py`
删除 `CONFIG_KEYS` 中的 `"llm_system_prompt"` 行（L23）。保留 `llm_user_prompt`。

### 3. `backend/app/api/config.py`
删除 `_to_config_out` 中的 `llm_system_prompt=...` 行（L26）。

### 4. `backend/app/services/llm.py`
- 删除 L66 `system_prompt = config.get("llm_system_prompt", "") or ""`
- L80 `{"role": "system", "content": system_prompt or SYSTEM_PROMPT}` 改回 `{"role": "system", "content": SYSTEM_PROMPT}`
- 保留 L67 `user_prompt_template` 与 L70-74 的 `build_user_prompt` 调用（这部分正确）

### 5. `backend/tests/test_config_api.py`
删除 L15 `assert data["llm_system_prompt"] == ""`。保留 L16 `llm_user_prompt` 断言。

### 6. `frontend/src/api/config.ts`
- `ConfigOut` 删除 `llm_system_prompt: string;`（L12）
- `ConfigUpdate` 的 Pick 联合中删除 `| 'llm_system_prompt'`（L26）

### 7. `frontend/src/pages/SettingsPage.tsx`
- zod schema 删除 `llm_system_prompt: z.string().optional(),`（L53）
- `defaultValues` 删除 `llm_system_prompt: '',`（L66）
- `form.reset({...})` 删除 `llm_system_prompt: data.llm_system_prompt,`（L89）
- **删除整个"LLM 系统提示词"Card**（L307-337，含 CardHeader/CardContent/FormField）
- 保留"LLM 用户提示词模板"Card（L339-374）不变

## Assumptions & Decisions

| 项 | 决策 | 理由 |
|---|---|---|
| 系统提示词 | 硬编码在 prompt.py，不可改 | 用户明确要求 |
| 用户提示词模板 | 可在设置页自定义，留空回退默认 | 用户明确要求 |
| 数据库残留 `llm_system_prompt` key | 不主动清理 | key-value 表，孤儿 key 不影响功能；清理需迁移脚本，违反 MVP 简单优先 |
| `build_user_prompt` 函数签名 | 不动 | `user_prompt_template` 参数是用户提示词的，正确 |
| Alembic 迁移 | 不需要 | key-value 表结构未变 |

## Verification

1. **后端测试**：`cd backend && .venv/bin/python -m pytest tests/ -q` → 9 passed
2. **后端 lint**：`.venv/bin/ruff check .` → All checks passed；`.venv/bin/black --check .` → unchanged
3. **前端构建**：`cd frontend && npm run build`（tsc + vite build）→ 通过
4. **前端 lint**：`npm run lint` → 0 错误（4 个 shadcn 固有 warning 不算）
5. **残留检查**：Grep `llm_system_prompt` 在 `backend/app` + `frontend/src` 下 → 0 匹配
6. **API 行为**：PUT `/api/config` 带 `llm_system_prompt` 字段应返回 422（Pydantic `extra="forbid"`）
