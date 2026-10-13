# 改造为内嵌批改智能体（去 ACP / 去 MCP + ChatGPT 订阅接入）可行性评估

> 评估日期：2026-10-11 ｜ 结论：**有条件可行**（详见 §7）

---

## 0. 摘要：先给结论

| 命题 | 判定 | 关键前提 / 说明 |
| --- | --- | --- |
| 去掉 ACP，改由后端直接驱动批改 | ✅ 可行 | 评分业务逻辑已与协议解耦，属"换适配器"而非重写 |
| 去掉 MCP，工具不再走 STDIO 协议 | ✅ 可行 | 工具底层已是普通 Python 函数，可进程内直调 |
| 完全自建 agent 循环（自研智能体） | ✅ 可行 | 但**必须**配 API key 或国内模型，与"订阅"互斥（§2.1） |
| 用 ChatGPT 订阅配额驱动批改 | ⚠️ 有条件 | 仅限"单人单账号、本机自用、走官方 Codex 运行时" |
| 把订阅做成面向多教师 / 对外服务的能力 | ❌ 不可行 | 官方已于 2026-08 明确定性 sub2api 式共享为滥用，触发风控封号 |
| 中国大陆作为商业部署形态 | ❌ 不推荐 | 不在支持地区 + 支付/发票/数据出境三重合规缺口 |

**一句话**：**"去 ACP/MCP"值得做、难度中等；"ChatGPT 订阅接入"技术上顺畅，但授权边界极窄，只能定位为"教师本人账号的本机可选能力"，不能作为产品主线。** 建议目标形态 = 一套薄 agent 内核 + 双 provider 轨道（订阅轨：Codex SDK，个人 BYO 账号；生产轨：OpenAI API 或国内模型）。

---

## 1. 现状盘点（基于代码实测）

### 1.1 三层结构：地基与适配器已经分离

| 层 | 内容 | 规模 | 是否依赖 ACP/MCP |
| --- | --- | --- | --- |
| 业务层 | `application/mcp_workflow.py`（注释明确"业务逻辑…错误由 API 适配层统一映射"）、`services/`（ocr、rubric、question_rubric、result_export…）、models、schemas | ~5k+ 行 | ❌ 不依赖，**可直接复用** |
| MCP 适配层（薄） | `mcp/server.py`（STDIO 工具暴露）、`api/mcp.py`（HTTP 端点）、`scripts/run-ai-marking-mcp`、handle/receipt 两张表 | ~1k 行 | 全部是协议壳 |
| ACP 适配层（厚） | `app/acp/*`（orchestrator/session/runs/registry/model_catalog/chat/workspace/events）＋ `api/acp.py`＋`api/acp_chat.py`＋`acp_worker.py`；前端 `api/acp.ts`、`api/acpChat.ts`、`AcpChatPanel`、`AcpRunProgress`、`AcpMarkingPanel`、`AcpAgentDirectory` 及测试 | 后端 ~6.2k 行；前端 ~3k 行 | 全部是协议壳 |

**这是本次评估最重要的发现**：rubric 解析、评分包构建、逐项校验（引用/满分/证据规则）、评分持久化、教师确认流程——全部在协议无关的业务层里。ACP/MCP 只是两个"驱动方式"。因此改造的核心工作量在**控制面替换**，不在业务重写。

### 1.2 一个容易被忽略的事实

全仓库检索确认：后端**不管理任何模型认证**（无 `OPENAI_API_KEY`、无 `auth.json` 处理），`codex-acp` 由 `npx` 启动并复用本机 `~/.codex/auth.json` 的登录态。

因此：**如果本机 codex CLI 已用 "Sign in with ChatGPT" 登录，当前 ACP 批改实际上已经在消耗订阅配额。** 也就是说"支持接入 ChatGPT 订阅模型"这一需求，现状已隐性满足一部分；用户真正新增的需求是"**不依赖 ACP/MCP 的内嵌形态**"。

### 1.3 可直接迁移的既有资产

- `.agents/skills/ai-marking-grader/SKILL.md`：批改流程权威源，本身就是 **Codex 原生 skill 格式**（Codex 0.86+ 原生支持 SKILL.md / SKILL.toml），换引擎后零改动复用；
- 工作区物化逻辑（`acp/workspace.py`：把源码 + skill.md 放进受控目录）——这正是"无 MCP"形态需要的东西；
- 前端 run 进度视图、SSE 事件流、runs 状态机与检查点语义。

---

## 2. 技术实现难度

### 2.1 关键约束：**"订阅"与"自建循环"互斥**

- ChatGPT 订阅的 OAuth token **不能**当 API key 直连 `api.openai.com`（Codex 走独立 entitlement 路径，只对 Codex surface 启用的模型生效）。
- 想消费订阅配额，**唯一合规技术通道是 Codex 运行时**，官方提供三种程序化入口：
  - Python SDK `pip install openai-codex`（需 Python 3.10+，本项目满足；底层控制 app-server 的 JSON-RPC，含 `AsyncCodex`）；
  - TypeScript SDK `@openai/codex-sdk`（Node 18+）；
  - `codex app-server`（文档化 JSON-RPC：threads / turns / 流式事件 / approvals / history）与 `codex exec`（脚本化单次执行）。
- 于是路线分野：

| 维度 | 路线 A：保留 Codex 引擎（可吃订阅） | 路线 B：完全自建循环（不能用订阅） |
| --- | --- | --- |
| 模型驱动者 | Codex 官方运行时（SDK / app-server / exec） | 你自己的 HTTP 客户端 + 自己写的 tool-calling 循环 |
| 凭证 | ChatGPT 订阅 OAuth（本机 `codex login`） | API key（按量付费）或国内模型 |
| 工具协议 | 进程内回合 + 工作区文件/CLI，**不需要 MCP** | 自定义工具 schema，进程内直调 |
| 去 ACP | ✅ 控制面从 ACP 换成 SDK | ✅ |
| 去 MCP | ✅ | ✅ |
| 难度 | **中** | **中高**（循环/流式/中断/恢复/重试/并发全自研） |

**唯一同时满足"订阅 + 无 ACP + 无 MCP"的形态**：把 Codex SDK 当作"受订阅支持的推理引擎"，后端做薄编排——评分包物化进工作区，一个或多个 turn 拿回结构化 JSON（SDK 支持 `output_schema` 强约束），后端沿用现有确定性校验入库、教师照常确认。这与现状高度同构：现在已经在物化工作区 + skill.md，只是把控制面从 ACP 换成 SDK。

### 2.2 工作量粗估

| 模块 | 动作 | 规模 |
| --- | --- | --- |
| `app/acp/*`、`api/acp*.py`、`acp_worker.py` | 删除/停用；保留语义（runs 状态机、检查点、SSE、权限档）迁入新内核 | 删 ~6.2k 行，迁移 ~0.5–1k 行 |
| `mcp/server.py`、`api/mcp.py`、`schemas/mcp.py`、handle/receipt 表 | 删除；`mcp_workflow.py` 更名 `grading_workflow.py` 原样保留 | 删 ~1k 行，保留 1.1k 行 |
| 新增 agent 内核 | 会话/回合、工具注册、流式事件、取消、失败恢复、provider 抽象 | 新增 ~1.5–3k 行 |
| 前端 | 删 ACP 安装目录 UI 与 chat 面板的协议部分；运行视图换数据源保留 | 删/改 ~3k 行 |
| 测试 | ACP fake agent、MCP 测试替换为内核与 provider 测试 | 同步改造 |

难度评级：**中等**。比"重写"轻得多——最大资产（评分业务与校验逻辑）无需重写。

### 2.3 主要技术卡点

1. **恢复/检查点**：ACP 的 `session/load` 由 Codex 提供；换 SDK 后用 thread 的 resume/fork 接口，语义需重新对齐（`acp_runs` 的检查点状态机可平移）。
2. **教师确认（waiting_for_teacher）**：ACP 的权限请求机制消失。两条替代：app-server 支持 approvals / permissions_request 自动应答；或退化为纯业务检查点（推荐——后端本来就有确定性校验兜底，不必依赖 agent 的审批语义）。
3. **结构化输出与超大上下文**：评分包上限 20 万字符、分页投喂；`output_schema` 只解决格式，业务校验（rubric item 覆盖、证据可定位、0 分规则）必须继续由后端强制。
4. **配额节流的工程化**：订阅为 5 小时滚动窗 + 周上限，且与 ChatGPT 共享同一池子；需要一个配额感知队列 + 降级策略。**这才是把订阅做成产品能力的真正难点**，而不是接口调用本身。

---

## 3. 关键依赖与限制

| 类别 | 内容 |
| --- | --- |
| 运行依赖 | 本机安装 Codex CLI（`npm i -g @openai/codex` 或 brew）；一次 `codex login`（订阅）或 `CODEX_API_KEY`；Python 3.10+ / Node 18+；网络可达 OpenAI 服务 |
| 配额限制 | 滚动 5 小时窗 + 周上限，与 ChatGPT/Work 共享池；Plus/Pro 可加购 credits；大批量批改的吞吐上限=订阅额度上限 |
| 模型面限制 | 只能用 Codex surface 启用的模型（GPT-5.x/6.x 系列 Codex 模型），**不能自选任意模型**；且是编码代理定位，非代码类作业（论文/论述题）的适配度需实测 |
| 形态限制 | "一人一账号一配额"是官方的合规基准线；任何"一个账号服务多人"的形态都会踩线 |
| 稳定性 | 无 SLA、政策随时可变、风控自动生效——不能成为业务连续性依赖 |

---

## 4. 授权与合规风险（重点）

### 4.1 OpenAI 官方口径（2026-08，Codex 负责人公开表态）

| 判定 | 内容 |
| --- | --- |
| ✅ 明确支持 | 个人以 "Sign in With ChatGPT" 使用订阅，**官方客户端与"支持该登录的开源第三方客户端"（Pi、OpenCode 等）均在受支持范围**；官方 SDK 文档明确支持把 Codex 嵌入应用、内部工具、工作流与 CI |
| ❌ 明确禁止 | 把订阅转成 API 流量**对外分发或供多人共享**（sub2api 类模式）——官方原话定性为"不被支持"，会触发反欺诈风控，表现为限额被压缩、账号受限 |
| ⚠️ 边界 | OAuth token 不能当 API key 直连 API；Codex 面外模型不可用 |

### 4.2 对本产品形态的风险映射

| 使用形态 | 判定 | 说明 |
| --- | --- | --- |
| 教师个人本机，1 人 1 账号，内嵌 SDK 自用 | ✅ 低风险 | 与官方认可的"OSS 客户端 + Sign in With ChatGPT"完全同形 |
| 多个教师共享一个订阅账号跑批改 | ❌ 高风险 | 账号共享 + 变相分发权益，违反条款 |
| 系统对外提供批改服务、用运营方订阅统一兜底 | ❌ 明确违规 | sub2api 同类模式，风控特征明显 |
| 校内服务器上一个账号为全校跑 | ❌ 高风险 | 同上 |

### 4.3 中国大陆特有的四个问题

1. **区域**：中国大陆及中国香港均不在 OpenAI 的支持地区列表内；在未列明地区使用或提供访问，可能导致账号被暂停或终止。
2. **支付与采购**：需要海外支付方式；无法开具人民币增值税发票，企业与学校的采购流程无法闭环。
3. **数据出境**：学生作业内容（可能含姓名、学号等个人信息）将传输至境外服务器，教育场景需按《个人信息保护法》评估合规路径并履行告知同意义务。
4. **不可控性**：以上任何一条都可能在无预警的情况下收紧，因此**订阅轨永远不能作为经营性的默认路径**。

---

## 5. 可行替代方案

### 方案 1（推荐主路径）：Provider 抽象 + 薄 agent 内核 + API key

以 OpenAI 兼容的 HTTP 接口为统一 provider 形态：海外用 OpenAI 官方 API（按量付费、商业用途受支持、有 SLA）；国内部署换国内模型（DeepSeek / Qwen / GLM / Kimi 等），价格低、可开票、数据境内、合规无碍。工具层直接注册业务层函数，彻底不引入 MCP。

### 方案 2（可选轨）：Codex SDK(订阅) 作为 provider

定位为"教师本人自备账号的**本机增强能力**"：不做容量承诺、不进合同、界面明示条款提示与风险自担。与方案 1 共用同一 provider 接口与 agent 内核，实现上是第三个适配器。

### 方案 3（最小改动）：不内嵌 agent，保留外部助手通道但降级协议

若目的只是摆脱 MCP 的复杂度：把工具降级为工作区内的 CLI 脚本 + 文件协议（agent 读文件、写 `assessment.json`，后端校验入库）。保留教师自定义工作流的灵活性，代价是体验仍是"专业工具"而非"一键批改"。

### 方案对比

| 维度 | 方案 1 (API 轨) | 方案 2 (订阅轨) | 方案 3 (外部助手) |
| --- | --- | --- | --- |
| 开发成本 | 中（内核一次投入） | 低（复用内核，加适配器） | 低 |
| 合规 | ✅ 商业可用 | ⚠️ 仅个人自用 | ✅（用户自带工具） |
| 教师体验 | ✅ 一键批改 | ✅ 一键批改 | ❌ 需自备编程助手 |
| 依赖 | 需 API 额度（可预算） | 受订阅配额/风控/地区制约 | 依赖外部客户端 |
| 建议定位 | **产品主线** | 个人可选项 | 过渡/可选 |

**推荐组合**：一套 agent 内核 + 三个 provider 适配器（Codex 订阅 / OpenAI API / 国内模型），默认走 API 轨；订阅轨以"BYO 账号"的形式作为实验开关存在。

---

## 6. 分阶段落地建议

| 阶段 | 内容 | 验收判据 |
| --- | --- | --- |
| 0. 边界冻结 | 产品文档明确"订阅仅限个人 BYO 账号，不做共享兜底" | 文案与设置页一致 |
| 1. Provider 抽象 | 抽 `ModelProvider` 接口 + 设置页（模型来源选择）；现有 ACP/MCP 双轨不动 | 现有测试全绿，新接口有单测 |
| 2. 无 MCP 工具层 + 自建循环 | `mcp_workflow` 函数注册为进程内工具；实现回合循环（先接一个 OpenAI 兼容 provider） | 一份含代码作业端到端批改通过，评分与现有校验规则逐项一致 |
| 3. 订阅轨 | 接 Codex SDK provider，验证 `output_schema`、thread 恢复、配额节流 | 连续批改 N 份作业不触发风控特征；配额耗尽可优雅降级 |
| 4. 下线 | 删 ACP/MCP 代码、表、UI、脚本与测试 | 单 worker 约束消失，部署步骤从"装 npx+codex+配置客户端"降为"填一个 key" |

---

## 7. 最终结论

**总判定：有条件可行。** 拆开看：

1. **"去 ACP / 去 MCP、把批改智能体内嵌进后端"——可行，且推荐做。** 技术难度中等：约 7k 行协议代码可删，1.1k 行业务逻辑与全部领域服务直接复用，新增内核 1.5–3k 行。改造后部署门槛显著降低（不再需要 npx 安装 agent、不再需要用户在外部客户端里配 MCP），这是对产品体验的实质提升。
2. **"接入 ChatGPT 订阅模型"——技术上顺畅，授权上有硬边界。**
   - 合规区间：**单人单账号 + 本机运行 + 走官方 Codex 运行时（SDK/app-server/exec）**。这完全在 OpenAI 明确表态支持的范围内。
   - 违规区间：**任何"一个订阅服务多人/对外提供"的形态**（共享账号、服务端统一兜底、转 API 分发）——官方已定性为滥用，风控会导致限额压缩乃至账号受限。
   - 中国大陆还有一个叠加问题：不在支持地区 + 无人民币发票 + 学生数据出境合规——**因此不适合作为商业主线**。
3. **不可逾越的三条红线**（无论实现方式）：
   - 不把订阅 token 当 API key 直连 `api.openai.com`；
   - 不做账号共享 / 多用户共享配额；
   - 不把订阅接入写进对外销售的产品承诺或合同。

**行动建议**：按 §6 的阶段 1–2 先行（这部分的收益不依赖任何外部前提），把 provider 抽象做出来；订阅轨作为阶段 3 的个人可选能力接入，不阻塞主线。这样即使 OpenAI 政策明天变化，产品也只损失一个可插拔适配器。

---

### 附：事实来源

- OpenAI Codex 官方 SDK 文档（`developers.openai.com/codex/sdk`）：Python `openai-codex` / TS `@openai/codex-sdk`、sandbox presets、thread 语义；
- Codex 负责人 2026-08 公开表态（sub2api 定性 + "Sign in With ChatGPT 官方与 OSS 客户端均受支持"）；
- OpenAI Help Center《Using Codex with your ChatGPT plan》：套餐与用量归属；
- 国内合规视角公开资料：支持地区列表、支付/发票/数据出境限制。
