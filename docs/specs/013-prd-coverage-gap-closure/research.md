# Research: PRD 覆盖缺口闭环

## R1: 订单发货端点设计（修复报告 6.1）

- **Decision**: 新增 `POST /order/{order_id}/ship`，请求体 `{tracking_no: str}`，仅允许 `paid` 状态发货（当前状态机 `VALID_TRANSITIONS["paid"]={"shipped","refunding"}` 已含 shipped 跃迁）。
- **Rationale**: 现有状态机已有 `paid->shipped` 合法跃迁，缺的只是触发端点与物流单号字段。沿用既有 `_check_transition` + 乐观锁 `update_status_optimistic` 机制，符合宪法 III（状态变更防重防错）。
- **Alternatives considered**: 引入 `processing` 中间态（PRD 文字提到 PAID/PROCESSING）-> 拒绝，当前模型无 processing 态，新增会扩大状态机复杂度且无业务必要性，YAGNI。
- **字段**: Order 模型新增 `tracking_no: str | None`（AES 不需要，物流单号非 PII）。

## R2: 流式端点复用 Agent（修复报告 6.2）

- **Decision**: `/session/message/stream` 改为先调 `agent_service.handle_message`（同步走 graph.invoke 全链路：注入检测/RAG/MCP/计费/trace），拿到完整 Agent 回复 content 后，再用 SSE 逐字 yield 流式输出。
- **Rationale**: agent_service 是同步架构（graph.invoke），真实 token 级流式需改 graph 为 astream，风险大、违宪法 V（不动核心链路）。先复用 handle_message 保证"流式内容来自 Agent 而非裸 LLM"，满足报告 6.2 核心诉求（经路由/检索/工具/安全/计费）。保留 session 归属校验与用户消息持久化。
- **Alternatives considered**: 直接改 graph 为 astream 事件流 -> 拒绝，改动核心 LangGraph 链路、风险高、超出"接线补齐"范围。

## R3: 注册短信验证码校验（修复报告 6.3）

- **Decision**: `RegisterRequest` 新增 `code: str` 字段；`auth_service.register` 先校验验证码（复用 `verify_code`/Redis `sms_code:{phone}`）再建租户；验证码无效抛 `InvalidCode`->401。
- **Rationale**: 后端已有 `/sms/send` 与 `send_verification_code`，验证码存 Redis `sms_code:{phone}`。注册时校验复用同一机制，零新增基础设施。前端 Register.vue 增加"获取验证码"步骤。
- **Alternatives considered**: 验证码改为必填导致既有调用方破坏 -> 既有调用方仅前端 Register.vue，本 feature 一并改造，无第三方调用，安全可做必填。

## R4: 前端 api 封装一致性

- **Decision**: 新增 api 函数遵循各文件既有约定--auth.ts/session.ts/billing.ts/order.ts 返回完整 axios response（调用方取 `.data`）；tenant.ts 已显式 `return res.data`，新增函数沿用 tenant.ts 风格（已解包）。
- **Rationale**: 报告第五章指出响应解包约定不统一但不影响联调正确性。本 feature 不重构既有约定（YAGNI），仅保持各文件内部一致。
- **Order/Refund 枚举**: order.ts 的 `Order.status` 补充为字面量联合类型（created/paid/shipped/completed/cancelled/refunding/refunded），RefundApproval.status 补全（pending/approved/rejected/refunding/refunded/cancelled）+ 中文映射。

## R5: 前端组件复用

- **Decision**: 坐席工作台 Trace 复用既有 `TraceTimeline` 组件（原仅 SessionDetail 用）；CSAT 新增 `CsatDialog`（el-dialog + el-rate）；留言新增 `LeaveMessageDialog`；快捷回复用 el-popover + 列表。
- **Rationale**: 报告 4.6 指出 TraceTimeline 组件已存在但未进工作台，直接复用即可。CSAT/留言为全新交互，按 Element Plus 既有风格新增轻量组件。

## R6: WebSocket 实时推送（范围决策）

- **Decision**: 本 feature 优先保证功能可用（保留 AgentConsole 现有轮询），WS 替换列为 P1 后续。若时间允许补 WS，但不阻塞 P0 验收。
- **Rationale**: 报告 4.4 WS 后端已实现，前端替换轮询为 WS 属体验实时性优化。功能可用性（认领/退回/Trace）优先于实时性。宪法 V 深度优先--先保核心闭环，再优化实时性。
