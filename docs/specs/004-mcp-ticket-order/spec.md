# Feature Specification: MCP Server + 工单/订单Agent

**Feature Branch**: `004-mcp-ticket-order`

**Created**: 2026-08-04

**Status**: Draft

**Input**: User description: "Day 5 — MCP Server + 工单/订单Agent：问工单进度能通过MCP协议查到数据；创建工单重试不产生重复记录。"

## User Scenarios & Testing

### User Story 1 — 工单查询（MCP真实调用）(Priority: P1) 🎯

用户在 Widget 输入"我的工单进度"，Agent 通过路由识别为 ticket_query → 检查槽位是否包含工单号 → 若无则反问工单号 → 用户提供工单号后通过 MCP 协议调用 get_ticket_status 工具查到工单数据 → Agent 生成自然语言回复。


**Independent Test**: Widget 问工单进度 → Agent 反问工单号 → 提供 TK-2024-001 → 返回工单状态"处理中"及详情。

**Acceptance Scenarios**:

1. **Given** 用户发送"工单TK-2024-001处理得怎么样了"，**When** Router识别intent=ticket_query且slots含ticket_id，**Then** Agent直接调用MCP工具返回工单状态。
2. **Given** 用户发送"帮我查一下我的工单"，**When** Router识别intent=ticket_query但slots无ticket_id，**Then** Agent反问"请提供您的工单编号"。
3. **Given** 用户提供的工单号不存在，**When** MCP工具返回空结果，**Then** Agent回复"未找到该工单，请确认工单号是否正确"。

---

### User Story 2 — 订单查询与退款（敏感操作）(Priority: P1)

用户查询订单状态或申请退款。退款属于敏感操作，无论 Agent 置信度多高都强制转人工处理（Human-in-the-loop）。

**Why this priority**: 退款=资金操作，写入"幂等+金额校验+强制人工"三道防线是 Constitution III 的落地证明。

**Independent Test**: Widget 问"订单ORD-2024001的状态"→返回订单详情。问"我要退款"→强制转人工提示。

**Acceptance Scenarios**:

1. **Given** 用户发送"订单ORD-2024001发货了吗"，**When** order_agent调用MCP工具，**Then** 返回订单状态"已发货"。
2. **Given** 用户发送"我要退款"，**When** Router识别intent=sensitive_action，**Then** 强制转人工（human_handoff），不经过order_agent。
3. **Given** 退款金额300元但订单实付200元，**When** 退款申请提交，**Then** 被拒绝且返回明确原因"退款金额超过订单实付金额"。

---

### User Story 3 — 幂等防护（防重复创建）(Priority: P2)

同一退款请求因网络重试发送两次（相同 client_request_id），系统只创建一条退款申请记录，第二次请求返回与第一次相同的结果。


**Independent Test**: 发两次带相同client_request_id的退款请求 → 只创建一条记录。

**Acceptance Scenarios**:

1. **Given** 第一次退款请求（client_request_id=REQ-001）成功创建，**When** 再次发送相同client_request_id的退款请求，**Then** 返回第一次的结果且不创建新记录。
2. **Given** 不同client_request_id的两次退款请求，**When** 分别发送，**Then** 各自创建独立的退款申请记录。

---

### User Story 4 — 工单/订单看板 (Priority: P2)

管理员在后台查看工单列表（按状态筛选）和订单列表，可对退款申请进行审批。

**Why this priority**: 看板是运营后台的基础能力，也是 Day 6 坐席工作台的 UI 基础。

**Independent Test**: 后台打开工单列表 → 按状态"处理中"筛选 → 看到筛选结果；打开订单列表 → 看到所有订单。

---

### Edge Cases

- 工单号格式错误（不符合 TK-YYYY-NNN 模式）→ 提示格式不正确。
- MCP 工具调用超时 → 返回"系统繁忙，请稍后重试"，不阻塞会话。
- 并发更新同一订单状态 → 乐观锁拦截，后提交者收到"订单状态已变更，请刷新重试"。
- 退款金额为 0 或负数 → 拒绝并提示"退款金额必须大于 0"。
- 对已退款订单再次申请退款 → 状态机拒绝非法跃迁。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 实现标准 MCP 协议（JSON-RPC 2.0）的工单工具集：get_ticket_status / create_ticket / update_ticket。
- **FR-002**: 系统 MUST 实现标准 MCP 协议的订单工具集：query_order / request_refund。
- **FR-003**: 工单 Agent MUST 在槽位缺少 ticket_id 时反问用户补全，不直接报错。
- **FR-004**: 订单 Agent MUST 在 intent=sensitive_action 时强制触发转人工中断，不自动处理退款。
- **FR-005**: 系统 MUST 支持幂等键（client_request_id），相同幂等键的重复请求只创建一条记录。
- **FR-006**: 退款金额 MUST NOT 超过订单实际支付金额，超额请求被拒绝并返回明确原因。
- **FR-007**: 订单状态机 MUST 校验跃迁合法性（如已退款订单不可再次退款），非法跃迁被拒绝。
- **FR-008**: 并发更新订单状态 MUST 使用乐观锁（version 字段），防止丢失更新。
- **FR-009**: 系统 MUST 提供工单列表接口（支持按状态筛选+分页）和订单列表接口。
- **FR-010**: 系统 MUST 提供退款审批接口（approve/reject）。
- **FR-011**: MCP 工具调用 MUST 校验调用方角色权限，agent 角色不可调用审批类工具。
- **FR-012**: 系统 MUST 包含 seed 脚本预置至少 5 条测试工单和 5 条测试订单。

### Key Entities

- **Ticket（工单）**: 工单编号、标题、状态(open/in_progress/resolved/closed)、优先级、指派人、所属租户。
- **Order（订单）**: 订单编号、金额、实付金额、状态(CREATED→PAID→SHIPPED→COMPLETED / REFUNDING→REFUNDED)、乐观锁版本号。
- **RefundApproval（退款审批）**: 关联订单、退款金额、原因、状态(pending/approved/rejected)、审批人、幂等键。
- **MCPTool（MCP工具定义）**: 工具名、描述、输入参数schema、所属Server、所需权限角色。

## Success Criteria

- **SC-001**: 用户从问"我的工单进度"到看到工单状态回复的完整对话不超过 3 轮。
- **SC-002**: 相同 client_request_id 的重复退款请求 100% 被幂等拦截（0 条重复记录）。
- **SC-003**: 退款超额请求 100% 被拒绝（0 条超额退款通过）。
- **SC-004**: 并发更新同一订单 5 次中有且仅有 1 次成功（乐观锁生效）。
- **SC-005**: 非工单/订单意图不触发 MCP 工具调用（不浪费资源）。
- **SC-006**: 后台看板页面能在 2 秒内加载并展示工单/订单列表。

## Assumptions

- MCP 协议使用 JSON-RPC 2.0 格式，通过函数直接调用（demo简化，不经过 stdin/stdout 子进程）。
- Mock 数据通过 seed 脚本预置到 MySQL，MCP 工具直查数据库（不接真实外部系统）。
- 订单乐观锁通过 SQLAlchemy `version_id_col` 或手动 WHERE version= 实现。
- 幂等键存储在 Redis（TTL=24h），键格式 `idempotency:{client_request_id}`。
- 退款审批人暂时为 admin 角色的任意用户（Day 6 引入坐席认领后才需要具体指派）。
