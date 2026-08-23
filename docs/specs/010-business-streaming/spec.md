# Feature Specification: 业务闭环与流式输出

**Feature Branch**: `010-business-streaming`

**Created**: 2026-08-06

**Status**: Draft

**Input**: User description: "业务闭环与流式输出：覆盖 1.9/1.10/1.11/2.3。1.9 知识库生命周期/质量检测/存储上限。1.10 订单撤销/分级审批/超时。1.11 计费补全（多模型折算/导出/降级）。2.3 SSE 流式输出。痛点：知识库无生命周期、退款不能撤销/无分级/无超时、计费只有简单计数、对话无流式。"

## Clarifications

### Session 2026-08-06

- Q: SSE 流式输出怎么实现？ -> A: 流式端点单独走（router+检索非流式 -> LLM 生成 litellm stream=True yield SSE），不复用 graph.invoke 同步路径，真流式首字延迟低。
- Q: 知识库存储上限按什么计算？ -> A: 按文档数（默认 50/租户，count 查询即可，简单可测）。
- Q: 多模型折算权重怎么定？ -> A: config 权重表（deepseek-chat=1.0x，其他按相对单价，可配置可扩展，demo 只用 deepseek 但展示折算机制）。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 订单撤销/分级审批/超时 (Priority: P1)

用户可撤销售后申请（审批前）；退款审批按金额分级（大额需管理员二次审批）；退款/订单超时自动取消或完成。当前退款只能审批不能撤销、无分级、无超时，影响资金安全与用户体验。

**Why this priority**：涉及资金状态变更（宪法原则 III），退款撤销/分级审批是资金安全硬伤，优先级最高。

**Independent Test**：提交退款 -> 撤销成功（状态 CANCELLED）；大额退款需二次审批；超时未审批自动取消。

**Acceptance Scenarios**：
1. **Given** 退款申请待审批，**When** 用户撤销，**Then** 状态转 CANCELLED，不可再审批。
2. **Given** 退款金额超阈值（如 1000 元），**When** 普通管理员审批，**Then** 需高级管理员二次审批才生效。
3. **Given** 退款申请超 7 天未审批，**When** 定时任务扫描，**Then** 自动取消该退款。

---

### User Story 2 - SSE 流式输出 (Priority: P1)

用户发消息后，回复逐字流式输出（SSE），而非等待全量回复后一次性返回。改善感知延迟（PRD P95 < 3s 目标），提升对话体验。

**Why this priority**：对话体验是 demo 直观感受，流式输出显著改善"等待感"，P1。

**Independent Test**：POST /message/stream -> 收到 SSE 事件流（data: chunk），逐字输出，最终完整回复。

**Acceptance Scenarios**：
1. **Given** 用户发送消息，**When** 调用流式端点，**Then** 返回 SSE 事件流，回复逐字/逐块输出。
2. **Given** 流式输出中，**When** 生成完成，**Then** 发送结束事件，完整回复已落库。
3. **Given** 流式过程中断（网络断），**When** 用户重发，**Then** 正常重新流式（不重复落库）。

---

### User Story 3 - 知识库生命周期/质量检测/存储上限 (Priority: P2)

文档上传时检测质量（乱码率/空内容率），超阈值标记待人工复核不进检索库；文档设有效期，到期自动摘除检索库；上传时校验租户存储容量上限。当前文档上传后无过期/无质量检测/无容量限制。

**Why this priority**：RAG 质量保障（过期/低质文档影响检索效果），依赖现有知识库链路，P2。

**Independent Test**：上传乱码文档 -> 标记待复核不进检索；文档到期 -> 摘除检索；超容量 -> 拒绝上传。

**Acceptance Scenarios**：
1. **Given** 上传乱码率超 30% 的文档，**When** 处理完成，**Then** 标记 quality_status=待人工复核，不进检索库。
2. **Given** 文档有效期到期，**When** 定时任务扫描，**Then** 从检索库摘除（标记过期）。
3. **Given** 租户存储已达上限，**When** 上传新文档，**Then** 拒绝上传并提示容量不足。

---

### User Story 4 - 计费补全 (Priority: P2)

多模型 token 折算（不同模型权重不同）、用量导出 CSV、账单逾期降级（限制 API 调用）。当前计费只有简单配额计数，无折算/导出/降级。

**Why this priority**：业务闭环补全，依赖现有计费基建，P2。

**Independent Test**：不同模型调用 -> 折算后配额递减；导出 CSV 含会话/意图/token/折算量；逾期 -> 降级限制。

**Acceptance Scenarios**：
1. **Given** 不同模型调用（如 deepseek-chat vs 其他），**When** 计费，**Then** 按模型权重折算后递减配额。
2. **Given** 管理员请求导出，**When** 调用导出端点，**Then** 返回 CSV 含会话/意图/token/折算量/时间。
3. **Given** 账单逾期 15 天，**When** 用户发消息，**Then** 被降级限制（返回降级提示）。

---

### Edge Cases

- 撤销已审批的退款 -> 拒绝（只有待审批状态可撤销）。
- 分级审批中大额退款已被普通管理员"批准" -> 仍需高级管理员确认才生效。
- 超时取消与用户撤销并发 -> 乐观锁防竞态。
- SSE 流式中 Agent 异常 -> 发送错误事件 + 兜底回复。
- SSE 流式与同步端点并存 -> 不影响现有 /session/message。
- 乱码率检测误判（正常文档被判乱码）-> 标记待复核（人工可手动通过）。
- 文档到期但仍在对话中被引用 -> 到期摘除后不再检索到（旧引用不变）。
- 存储容量计算 -> 按文档切片数/向量数估算（plan 阶段定精确算法）。
- 多模型折算权重表 -> 可配置（plan 阶段定默认值）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**：系统 MUST 支持撤销售后申请（仅待审批状态可撤销，状态转 CANCELLED）。
- **FR-002**：退款审批 MUST 按金额分级（超阈值需高级管理员二次审批，宪法原则 III）。
- **FR-003**：系统 MUST 定时将超时未审批的退款自动取消（默认 7 天）。
- **FR-004**：系统 MUST 提供 SSE 流式输出端点，流式端点单独走（router+检索非流式 -> LLM 生成 litellm stream=True 逐字 yield SSE），完成后落库，不复用 graph.invoke 同步路径。
- **FR-005**：流式输出 MUST NOT 影响现有同步消息端点（两者并存）。
- **FR-006**：文档上传时 MUST 检测质量（乱码率/空内容率），超阈值标记待人工复核不进检索库。
- **FR-007**：文档 MUST 设有效期，到期自动从检索库摘除。
- **FR-008**：上传时 MUST 校验租户存储容量上限（按文档数，默认 50/租户），超限拒绝上传。
- **FR-009**：计费 MUST 按模型权重折算 token 用量（config 权重表，deepseek-chat=1.0x，其他模型按相对单价折算，可配置可扩展）。
- **FR-010**：系统 MUST 支持用量导出 CSV（含会话/意图/token/折算量/时间）。
- **FR-011**：账单逾期 MUST 自动降级（限制 API 调用，返回降级提示）。
- **FR-012**：所有写操作（撤销/审批/降级）MUST 保持幂等 + 乐观锁防并发（宪法原则 III）。
- **FR-013**：所有操作 MUST 携带 tenant_id 过滤（宪法原则 II）。

### Key Entities

- **退款审批分级**：退款金额 vs 审批阈值 -> 决定审批层级（普通/高级管理员）。
- **文档质量状态**：ready（正常）/ pending_review（待人工复核，乱码超阈值）/ expired（过期摘除）。
- **模型折算权重表**：model -> weight（如 deepseek-chat=1.0，其他模型按 token 单价折算）。
- **SSE 事件流**：data: {chunk} 逐字/逐块 + [DONE] 结束标记。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：待审批退款 100% 可撤销；大额退款 100% 需二次审批；超时 100% 自动取消。
- **SC-002**：流式端点 100% 逐字输出 SSE 事件流，首字延迟 < 2 秒。
- **SC-003**：乱码文档 100% 被标记待复核不进检索；到期文档 100% 摘除；超容量 100% 拒绝上传。
- **SC-004**：多模型调用 100% 按权重折算；CSV 导出含完整字段；逾期 100% 降级。
- **SC-005**：所有操作带 tenant_id 过滤，无跨租户影响。

## Assumptions

- 退款撤销端点：POST /api/v1/order/refunds/{id}/cancel（仅 pending 状态可撤销）。
- 分级审批阈值：默认 1000 元（可配置），超阈值需 admin 角色（非 agent）二次审批。
- 超时取消：后台线程定时扫描（与 007 cleanup_tasks 同模式），默认 7 天。
- SSE 端点：POST /api/v1/session/message/stream，流式端点单独走（router+检索非流式 -> LLM 生成 litellm stream=True yield SSE chunks），不复用 graph.invoke 同步路径，完成后落库。
- 文档有效期：默认 90 天（knowledge_document 加 expires_at 字段，plan 阶段定迁移）。
- 质量检测：切片后统计乱码率（非 UTF-8 字符占比），超 30% 标记 pending_review。
- 存储上限：按租户文档数（默认 50，count 查询即可，简单可测）。
- 模型折算：config 权重表（deepseek-chat=1.0x，其他按相对 token 单价，可配置可扩展，demo 只用 deepseek 但展示折算机制）。
- 用量导出：GET /api/v1/billing/usage/export -> CSV 文件下载。
- 账单逾期降级：billing_invoice 过期 15 天 -> 租户 status=degraded（限流/拒绝消息）。