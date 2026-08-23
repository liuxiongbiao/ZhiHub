# Research: MCP 协议化与智能体安全加固

> Phase 0 产出：解决 Technical Context 中的所有未知项与技术选型。

## R1: MCP 协议选型 -- JSON-RPC 2.0 信封 vs MCP 官方 SDK

**Decision**: JSON-RPC 2.0 信封 + HTTP 传输（复用现有 `/api/v1/mcp/tools/call` 端点）

**Rationale**:
- MCP 官方 SDK（Python `mcp` 包）使用 stdio/SSE 传输，要求独立 server 进程，对 demo 单体部署过重，且 Agent 进程内调用需跨进程通信
- HTTP 传输与现有 FastAPI 端点兼容，Agent 侧 `mcp_service.call_tool` 调用入口不变，只是内部封装信封
- 信封结构标准化：
  - 请求：`{"jsonrpc":"2.0","id":"<uuid>","method":"<tool_name>","params":{...}}`
  - 成功：`{"jsonrpc":"2.0","id":"<uuid>","result":{...}}`
  - 错误：`{"jsonrpc":"2.0","id":"<uuid>","error":{"code":<int>,"message":"<str>"}}`

**Alternatives considered**:
- MCP 官方 SDK（stdio 传输）：协议最"真"，但需独立 server 进程 + stdio 传输层，demo 复杂度高，且与现有 HTTP 架构不兼容。否决。
- 纯函数调用（现状）：无法支撑"标准协议"叙事，宪法原则 V 明确 MCP 真实协议不可砍。否决。

## R2: 双因子校验如何接入现有 Agent 而不破坏多轮槽位流程

**Decision**: `phone` 作为额外槽位，与 `ticket_no`/`order_no` 同级；双槽位齐全才调工具；身份校验在 MCP server 层

**Rationale**:
- Agent 槽位收集流程不变（缺槽位反问），`phone` 与 `ticket_no` 都是槽位，router 提取或正则补充，缺失则反问
- 身份校验集中在 server 层（`ticket_server` 校验传入 phone 与工单绑定 phone 后4位），符合宪法原则 I（Agent 不直接查 DB 校验身份）
- 校验不匹配返回"未查询到匹配信息"，不区分"工单不存在"与"身份不匹配"，防枚举攻击
- 校验附加 tenant_id 过滤（宪法原则 II），跨租户查询同样返回"未查询到"

**Alternatives considered**:
- 校验在 Agent 层：违反分层（Agent 不应直接查 DB），且 Agent 难以维护手机号绑定关系。否决。
- 校验在 API 层：Agent 调用是内部流程，无独立终端用户 API 入口。不适用。

## R3: 熔断状态存储

**Decision**: Redis，key `mcp:circuit:{tool_name}` 存 JSON `{state, fail_count, recover_count, last_failed_at}`

**Rationale**:
- 复用现有 Redis 基础设施，原子 INCR 计数，TTL 防止死锁
- 熔断状态高频读写（每次工具调用都检查），Redis 性能远优于 MySQL
- 多 worker 共享状态（若未来多进程），Redis 天然共享

**Alternatives considered**:
- MySQL：高频读写不适合，且熔断状态是瞬态无需持久化。否决。
- 进程内存：多 worker 不共享，熔断状态不一致。否决。

## R4: Prompt 注入检测规则设计

**Decision**: 规则库（关键词 + 正则模式），命中标记 risk_level（low/medium/high），high 转人工

**Rationale**:
- 轻量前置防御，无 ML 模型依赖，demo 够用
- 规则可枚举（中英文）：
  - `忽略.*指令` / `ignore.*previous.*instructions`
  - `你现在是` / `you are now`
  - `system prompt` / `系统提示词`
  - `扮演.*角色` / `pretend.*to be`
  - `输出.*规则` / `reveal.*rules`
- 命中记录 SecurityIncident（rule_hit, input_preview, risk_level）供事后审查与规则优化
- high 风险直接转人工；medium 标记但继续（供观察）；low 忽略

**Alternatives considered**:
- ML 分类器：需训练数据与模型部署，demo 过重。否决。
- LLM 二次判断：成本高且 LLM 本身可能被注入，引入新攻击面。否决。

## R5: 熔断恢复机制 -- half-open 状态

**Decision**: 三态熔断器（closed/open/half-open），熔断后 half-open 允许少量探测，连续 3 次成功才解除

**Rationale**:
- 避免熔断后立即全量恢复又被打回（下游可能未完全恢复）
- half-open 是熔断器标准模式（Hystrix/Resilience4j 同款）：熔断超时后放一个探测请求，成功则 recover_count++，达阈值转 closed；失败则重回 open
- 探测间隔默认 30 秒（避免半开状态下大量请求涌入）

**状态流转**:
- closed（正常）-> 连续 5 次失败 -> open（熔断，返回兜底）
- open -> 超时 30s -> half-open（放探测）
- half-open -> 连续 3 次成功 -> closed
- half-open -> 任意失败 -> open（重置计时）

## R6: 协议化对现有幂等/权限的影响

**Decision**: 协议化只改信封封装，不改业务逻辑；幂等键（client_request_id）、权限矩阵、入参校验、退款金额上限全部保留

**Rationale**:
- JSON-RPC 信封是传输层封装，`params` 内容仍是原参数（含 client_request_id）
- mcp_service.call_tool 内部：解封信封 -> 权限校验 -> 入参校验 -> 幂等检查 -> 调 server -> 封装响应
- 宪法原则 III（资金/状态防重防错）不受影响