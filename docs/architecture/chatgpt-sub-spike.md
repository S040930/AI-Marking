# ChatGPT 订阅通道 spike 报告（阶段 6 · A 段）

> 执行时间：2026-10-17 19:12（A 段）/ 19:41（B 段，经应用内 OAuth 端点）
> 脚本：`backend/scripts/spike_chatgpt_sub.py`；实装后的应用内登录见 `langgraph-grading.md`
> 结论：**A/B 两段均通过（implement）**，阶段 7 已实装并完成真实作业验收。
> 原始证据：`backend/.spike/spike-20261017-191231.json`（本地，已 gitignore，含账号后四位等痕迹）

## 1. 方法与成本

- 用本机 `~/.codex/auth.json` 的**现成 ChatGPT token**（`auth_mode=chatgpt`，plan `plus`，账号 `…c276`），不改生产代码。
- 全程 38 次 Responses 调用 + 3 次补测，合计 **1,713 tokens**（1,443 in / 270 out），对 5 小时/周配额影响可忽略。
- 出口：本机系统代理 `127.0.0.1:7890`。注意：WorkBuddy 默认环境代理 `127.0.0.1:51375` **无法访问 `chatgpt.com`**（连接超时），脚本因此内置系统代理探测。
- 所有输出经 `redact()`：JWT / refresh token / Bearer / 账号 UUID 均脱敏；`x-codex-turn-state` 只记长度。

## 2. 端点与常量（取自 codex-cli 0.162.0 二进制常量表 + 实测）

| 项 | 值 |
|---|---|
| Responses 端点 | `POST https://chatgpt.com/backend-api/codex/responses`（首个路径即命中，备选 `/backend-api/codex/v1/responses` 未用上） |
| 模型目录 | `GET https://chatgpt.com/backend-api/codex/models`，**必须带 `client_version` 查询参数**，否则 400 `Field required` |
| OAuth authorize | `https://auth.openai.com/oauth/authorize`，client_id `app_EMoamEEZ73f0CkXaXp7hrann` |
| OAuth token | `https://auth.openai.com/oauth/token`（authorization_code / refresh_token 均走此端点） |
| 回调 | `http://localhost:1455/auth/callback`（`auth/callback` 常量见二进制） |
| 订阅侧模型 | `gpt-6.1-sol`、`gpt-6-astra`、`gpt-6-sol`、`gpt-6-luna`、`gpt-reserve`、`gpt-5.6-*`、`codex-auto-review` —— **与官方 API 的 `gpt-5.1-codex` 等完全不同** |

## 3. A 段结果

### A1 最小必填头 / 体集合 ✅

- 除 `Authorization` 外，**所有头都可省略**：逐个移除 `chatgpt-account-id` / `originator` / `session_id` / `OpenAI-Beta` / `User-Agent` 全部仍 200。
- 极简头集合（`Authorization` + `Content-Type` + `Accept`）实测 **200**。
- 极简体（`model` + `input` + `store` + `stream`）实测 **200**；`instructions` / `tools` / `reasoning` / `include` / `prompt_cache_key` / `parallel_tool_calls` / `tool_choice` 均可省。
- `store` 是唯一必需的体字段，且**必须显式为 `false`**：省略或传 `true` 都返回 400 `Store must be set to false`。
- 基线与极简调用均可重复稳定复现（连续 2 次 200）。

> 含义：会话/身份并不靠头绑定，服务端只认 token 与 `store=false`。生产适配器无需伪装 CLI 头。

### A2 自定义 tool function-call 往返（核心）✅

| 检查项 | 结果 |
|---|---|
| 收到 `function_call` | ✅ `probe_echo`，arguments `{"value":"PING"}` 完整可解析 |
| 回传 `function_call_output` | ✅ 200，末轮文本 `PONG` |
| `reasoning` item（含 `encrypted_content`） | ❌ **0 个**（effort=low 与 effort=high 均无） |
| `x-codex-turn-state` | ✅ 每回合返回（约 780 字符，不透明） |
| 回传 turn-state 续回合 | ✅ 200 |

> **风险 3 实锤**：官方通道“`store=false` + `include: reasoning.encrypted_content` 回传”的方案，在订阅端点上**拿不到 reasoning item**。续会话状态改由 `x-codex-turn-state` 承载（每回合响应头下发，下一回合回传）。工具往返本身不依赖 reasoning 回传，可正常工作。

### A3 `originator` 接受度 ✅

| 变体 | originator | 状态 |
|---|---|---|
| 自报 | `ai_marking_spike` | 200 |
| 参考 | `codex_cli_rs` | 200 |
| 缺省 | 不发送 | 200 |

自报应用名被接受，**不需要也不应该伪装 `codex_cli_rs`**（默认决策 4 成立，无回退）。

### A4 usage / 耗时 / 配额痕迹 ✅

- usage 结构完整：`input_tokens` / `output_tokens` / `total_tokens` / `*_details`，另附**逐 item 的 `attribution`**（体积大，落库建议只取前三项）。
- 耗时：TTFB 约 0.65–0.82 s；纯文本回合 1.3–1.5 s；含工具回合 3.5–4.4 s。
- 限流/配额响应头（`x-codex-*`）：
  - `plan-type=plus`、`active-limit=premium`
  - `primary-window-minutes=10080`（**周窗**）、`primary-used-percent=3`
  - `primary-reset-after-seconds≈515828`、`primary-reset-at=1792751365`
  - `credits-has-credits=False`、`credits-balance=0`、`credits-unlimited=False`
  - `safety-buffering-enabled=true`、`safety-buffering-faster-model=gpt-6-luna`
- **无 `x-request-id`**：排障只能靠状态码 + message。

### A5 错误路径 ✅

| 探针 | 状态 | 错误摘要 |
|---|---|---|
| 伪造 token | 401 | `Could not parse your authentication token. Please try signing in again.` |
| 缺 Authorization | 401 | `We got your request, but your ChatGPT login did not make it to this service.` |
| 不存在的 model | 400 | `The 'gpt-spike-nonexistent' model is not supported when using Codex with a ChatGPT account.` |
| 非法 effort | 400 | `invalid_value: 'ultra' … Supported values are: 'none','minimal','low','medium','high','xhigh','max'` |

均为 `{"error":{"message":…}}` 结构，可直接映射到 `AgentProviderError`：401→`auth_required`、400→`invalid_request`、429→`rate_limited`（本轮未触发）。

### A6 可用 model / effort ✅（但目录声明不可信）

- 目录端点（`client_version=0.162.0`）返回 9 个模型；实测前 4 个全部 200：`gpt-6.1-sol`、`gpt-6-astra`、`gpt-6-sol`、`gpt-6-luna`。
- `gpt-6.1-sol` 实测接受的 effort：`low` / `medium` / `high` / `xhigh` / `max`。
- **被拒**：`ultra`（目录声明支持，实测 400）、`none`、`minimal`（错误信息说支持，但对该模型 400 `Unsupported value`）。

> 含义：目录里的 `supported_reasoning_levels` **会掺假**，不能直接当 UI 选项；必须按模型维护“实测白名单”，或首次使用时探测并缓存。

## 4. 与实施计划的偏差（阶段 7 必须照此调整）

| # | 原计划假设 | 实测事实 | 实装影响 |
|---|---|---|---|
| 1 | 模型清单可静态写死（`gpt-5.1-codex` 等） | 订阅侧是 `gpt-6.*` 系列，且目录需 `client_version` | `chatgpt_sub.list_models()` 必须请求目录并缓存；不得复用官方通道常量 |
| 2 | 需要 `chatgpt-account-id` / `originator` / `session_id` | 全部可省（多账号场景仍建议带 account-id） | 适配器头集合可极简，降低风控面 |
| 3 | `store=false` + encrypted reasoning 回传 | 订阅端点**不返回** reasoning item；`store` 必须显式 `false` | `agent_state` 改为保存 `x-codex-turn-state`；不请求 `include` |
| 4 | `originator` 自报可能被拒 | 自报、缺省都 200 | 无回退分支；默认 `originator: ai_marking` |
| 5 | 目录 `supported_reasoning_levels` 即真值 | `ultra` 声明支持但实测拒绝 | UI effort 选项按实测白名单收敛：`low/medium/high/xhigh/max` |
| 6 | 可用 `x-request-id` 排障 | 无该头 | 错误落库带 status + message + 时间；不指望 request-id |

## 5. 阶段 7 实装清单（据此收敛）

1. `providers/chatgpt_sub.py`：`base_url=https://chatgpt.com/backend-api/codex`，路径 `/responses`；body 强制 `store=false`；复用 `providers/sse.py` 解析；`ProviderTurnCompleted` 里额外带 `turn_state`（新增字段或复用 `provider_state`）。
2. `agent_state` 增键 `turn_state`：每回合用响应头覆盖；`runner` 续跑/恢复时回传。这是**新的持久化契约**，需迁移无关（JSON 字段已存在）。
3. `list_models()`：`GET /models?client_version=<codex 版本>`；`ModelOption.efforts` 用实测白名单（`low/medium/high/xhigh/max`），失败回退静态 4 项。
4. `chatgpt_oauth.py`：PKCE + `127.0.0.1:1455` 回调 + code 交换 + refresh 轮换；刷新失败 → `needs_auth`。
5. `provider_config.py`：`chatgpt_tokens` 存 `{access, refresh, id_token, account_id, expires_at, plan}`；对外只暴露 `plan` 与账号后四位；`available` 由 `configured && !expired` 决定（去掉硬编码 `False`）。
6. 设置页订阅卡：显示计划类型、周配额已用百分比与重置时间（来自 `x-codex-primary-*`），登录/退出按钮。
7. 测试：httpx mock 覆盖目录 400/200、正常回合、工具往返、401、400、429；`test_provider_config` 增加 token 过期判定。
8. 验收：真实 OAuth 登录一次 + 一份作业批改。✅ **已完成**（2026-10-17）：
   登录 → 通道测试返回 9 个模型 → 作业 #12 批改 run 7 → 检查点答复续跑 →
   `completed`，usage 152,577 in / 10,498 out，建议 95/100 落库、状态
   `ready_for_review`。

## 6. B 段（已执行，经应用内 OAuth 端点）

2026-10-17 19:41 用实装后的 `POST /api/agent/providers/chatgpt_sub/oauth/{start,status}` 完成：

| 检查项 | 结果 |
|---|---|
| PKCE 授权 + `localhost:1455` 回调 | ✅ 浏览器完成登录，`status=completed` |
| 授权码换令牌 | ✅ 令牌落库（plan=`plus`，账号 `…c276`，有效期约 10 天） |
| refresh 轮换 | ✅ 人工把 `expires_at` 改为过期后触发刷新：新 `refresh_token` 已回写，新到期时间 +10 天 |
| 连续调用风控 | ✅ 真实批改 run 共 9 个模型回合（含工具调用）全部 200，无 429 |

> 巡检发现并修复一个真实缺陷：检查点暂停时 `request_teacher_confirmation`
> 的 `function_call` 没有配对 `function_call_output`，续跑时订阅端以
> `No tool output found for function call` 拒绝整段 input（官方通道不报）。
> 引擎现在在检查点前补一条 `{"status":"pending_teacher_confirmation"}` 的
> output，续跑历史因此完整。

## 7. 复现

```bash
cd backend
.venv/bin/python scripts/spike_chatgpt_sub.py --dry-run          # 只打印计划
.venv/bin/python scripts/spike_chatgpt_sub.py --section a --verbose
.venv/bin/python scripts/spike_chatgpt_sub.py --section a \
  --md-out ../docs/architecture/chatgpt-sub-spike.md             # 覆盖本文件（会丢手写章节）
```

注意：`--md-out` 会整文件覆盖；本文件的手写章节需在自动报告之上人工合并。

## 8. 结论

A/B 两段全部通过：**工具往返可用、自报身份被接受、usage/配额可见、错误可诊断、
OAuth 登录与 refresh 轮换可用、真实作业端到端跑通**。
唯一实质偏差是 reasoning 回传不可用，已用 `x-codex-turn-state` 替代并随检查点持久化。
阶段 7 适配器已实装（`providers/chatgpt_sub.py`、`providers/chatgpt_oauth.py`、
OAuth 三端点、设置页登录卡），421 个后端测试与 129 个前端测试通过。
