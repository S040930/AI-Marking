# Codex-only ACP 批改与作业上下文对话

> 状态：首期实现。公开 ACP 产品入口只支持官方 `codex-acp`；通用 ACP
> session、Registry 快照、worker 和 SSE 内核保留为未来内部扩展点。

## 1. 产品边界

教师可以为每次自动批改或作业上下文对话选择：

- 当前已安装 Codex ACP Agent 声明的模型；
- 该模型当前声明的 `thought_level` 思考档位；
- 标准或快速速度模式；
- `Ask for approval` 或 `Approve for me` 权限档位。

前端不展示 Claude、Gemini、OpenCode、自定义 Agent 或默认 Agent 选择器。
后端有效白名单固定为 `codex-acp`，旧的 `agent_id` 创建字段由 schema 拒绝。
`Full access` 不是有效权限值。

模型、思考和速度不使用硬编码清单，也不通过浏览器传入 ACP 原始 option ID。
目录来自当前安装版本的 `session/new`；模型切换后重新读取完整
`configOptions`，再验证该模型的思考与速度能力。

## 2. ACP 与 MCP 的分工

ACP 负责 Codex 进程、session 生命周期、动态配置、权限请求、事件和批改
worker；AI-Marking MCP 负责读取评分包、保存评分建议和既有教师确认流程。
学生代码与 Agent 工具调用从物化的作业工作区启动，应用固定传入以下 Codex 原生配置：

- `sandbox_mode=workspace-write`；
- `sandbox_workspace_write.network_access=false`；
- `ask` 与 `auto_review` 由当前运行时权限档位处理；
- 两种档位都保持相同的原生工作区与禁网边界；
- 应用不创建外层 VM、文件路径白名单或认证探针，也不注入 `Full access`。
  其他可见范围和沙箱语义由 Codex 原生实现决定。

### 部署约束（单 worker）

ACP 对话面板（`/api/acp/chat`）的权限答复依赖 API 进程内的
`asyncio.Future` 注册表：教师批准必须到达持有该 agent 进程的**同一个
API 进程**。因此 API **必须单 worker 启动**（`uvicorn --workers 1`）；
多 worker 下教师批准可能路由到错误进程，返回 409「权限请求不存在或已答复」。
自动批改 worker（`python -m app.acp_worker`）是独立进程，不受此约束。

## 3. 动态 Codex 配置目录

共享接口：

```text
GET /api/acp/codex/configuration?model_id=<optional>
```

响应包含 `agent_version`、当前模型、模型列表、思考档位、速度档位和能力
错误。思考只识别 `category == "thought_level"` 的 select；模型只识别
`category == "model"` 的 select。速度只识别 `fast_mode` 布尔项，或包含
`standard`/`fast` 的 select 项。其他 Agent 配置永远不透传到网页。

探测会话不注入 MCP，仅使用受控工作区。缓存键是 `codex-acp + 安装版本 +
model_id`，TTL 为 10 分钟；安装、卸载、连接测试成功和版本变化会使缓存失效。
探测失败不使用过期硬编码模型清单，返回能力错误与标准模式，前端只允许
Agent 默认模型/标准速度语义。

应用配置严格遵循：

```text
model → 重新读取 configOptions → thought_level → 重新读取 configOptions → speed
```

`session/set_config_option` 在 Codex 路径使用严格模式，必须返回完整
`SetSessionConfigOptionResponse`；拒绝或空响应会抛出协议错误。收到
`config_option_update` 时只刷新内存能力快照并记录安全摘要，不接受它修改沙箱、
网络、MCP 或工作目录。

## 4. 持久化与状态

`acp_runs` 与 `acp_chat_sessions` 在当前数据库迁移链中各有：

- `permission_mode`：`ask` 或 `auto_review`；
- `codex_config`：教师期望配置，旧数据为 `{}`；
- `applied_codex_config`：Agent 最近确认实际生效的配置；
- `pending_codex_config`：忙碌时等待安全点应用的配置。

快照只保存产品字段与后端需要的 option 元数据：

```json
{
  "model_id": "<agent-value>",
  "reasoning_effort": "high",
  "speed_mode": "fast",
  "option_ids": {
    "model": "<agent-option-id>",
    "reasoning_effort": "<agent-option-id>",
    "speed_mode": "<agent-option-id>"
  },
  "catalog_version": "codex-acp@<installed-version>"
}
```

事件只记录标准化配置摘要和状态，不记录 Token、认证信息、原始配置文件或
主机绝对路径。

## 5. 创建与更新 API

创建入口：

```text
POST /api/acp/runs
POST /api/acp/chat/sessions
```

请求使用：

```json
{
  "submission_id": 42,
  "permission_mode": "ask",
  "codex_config": {
    "model_id": "<optional>",
    "reasoning_effort": "<optional>",
    "speed_mode": "standard",
    "fast_confirmed": false
  }
}
```

缺省模型和思考档位由 Agent 当前目录填充；`fast` 必须带
`fast_confirmed=true`。所有字段均禁止额外属性，因此浏览器不能提交任意
ACP option ID 或沙箱字段。

更新入口：

```text
PATCH /api/acp/runs/{run_id}/codex-configuration
PATCH /api/acp/chat/sessions/{chat_id}/codex-configuration
```

更新响应同时提供 `desired_config`、`applied_config`、`pending_config` 和
`effective_at`，`run`/session 内仍保留兼容的 `codex_config` 字段。

关闭与删除：

```text
DELETE /api/acp/chat/sessions/{chat_id}            # 关闭会话(保留历史)
DELETE /api/acp/chat/sessions/{chat_id}/permanent  # 永久删除(不可恢复)
```

关闭是终态转换：停止 turn 与 agent 进程组，行与事件转录保留供历史查看。
永久删除在此基础上继续删除会话行并显式清除其全部事件(不依赖外键级联)，
随后仅在目录与该 chat ID 精确对应(resolve 后拒绝符号链接、父目录必须是
受控 `acp-chat-workspaces` 根、且与行内 `workspace_path` 记录一致)时清理
专属工作区；校验失败则跳过目录删除并记录日志。删除后打开中的 SSE 流会把
"会话不存在"视为终止态并发送 `: done`。

应用时序：

| 状态 | 行为 |
| --- | --- |
| run `queued`/`starting` | 直接替换期望配置，worker 启动 session 时应用 |
| run `running`/`waiting_for_teacher` | 写入 pending，下一安全 ACP 回合应用 |
| chat 空闲且已有 session | 立即在同一 session 应用 |
| chat `running`/`waiting_permission` | 排队到当前 prompt 返回之后、回到 idle 之前 |
| terminal run 或 closed/error chat | 返回 409 |
| Agent 拒绝 | 清空 pending，恢复上次 applied；记录 `configuration_failed`，聊天返回 409 |

模型切换后旧思考或速度不适用时，采用新模型目录中的 Agent 默认值，返回修正
后的快照并记录 `configuration_adjusted`。`standard → fast` 必须重新确认；
`fast → standard` 和保持 fast 不重复确认。

## 6. worker、恢复与审计

自动批改 worker 领取租约后创建或恢复 ACP session，应用持久化配置，再发送评分
prompt。检查点续跑优先 `session/load`；worker 重启、租约过期或 Agent EOF 时，
pending 仍留在数据库，重新协商成功前不会写入 applied。对话恢复同样重新读取
Agent 当前目录；不再支持的配置回退到 Agent 默认值并记录调整事件。

现有教师一致性检查点、权限请求、评分建议保存和最终成绩确认流程不因配置改变。
SSE 继续回放结构化事件，并额外传递配置应用、排队、调整、失败和 Agent 动态
能力摘要事件。

## 7. 安装与 Registry 安全

- Registry 元数据缓存 6 小时，安装使用精确版本；安装快照记录包、版本、命令和环境。
- 首期默认白名单唯一条目为 `codex-acp` / `@agentclientprotocol/codex-acp` / `npx`。
- 本地审核校验 Agent ID、仓库身份、包身份、发行类型和精确版本。
- 连接测试成功会清空配置目录缓存；卸载和安装新版本也会清空缓存。
- 安装缓存位于 `uploads/acp-cache/agents/<agent-id>/<version>`，不写项目源码。

设置页只提供 Codex 安装、更新和连接测试。连接测试就绪后即可发起批改；页面固定说明
使用 Codex 原生工作区沙箱且网络已关闭。配置目录不可用时，
页面明确展示能力错误，不伪造模型或快速模式支持。

## 8. 测试与资源边界

`backend/tests/acp_fake_agent.py` 提供模型 A/B、模型切换后的动态思考/速度、
完整 set-config 响应、配置拒绝、动态 `config_option_update` 和 prompt 延迟。
后端测试覆盖目录探测、缓存、创建校验、Fast 确认、空闲即时应用、忙碌排队、
run 状态更新、失败回退、权限和恢复；前端测试覆盖控件、滑块提交、Fast 确认、
上传自动启动、运行配置和聊天历史。

运行时约束：单次目录探测最多等待 60 秒；ACP turn 默认最多 30 分钟；配置更新
只保留小型 JSON 快照，事件流按既有 64KB 单事件/5MB 转录上限。探测和安装不会
加载评分数据集；上传/OCR 等数据管线继续按项目既有批处理与内存边界运行。

最终校验命令：

```bash
ruff check backend
cd backend && .venv/bin/pytest -q
cd frontend && npm run lint
cd frontend && npm run test:run
cd frontend && npm run build
```
