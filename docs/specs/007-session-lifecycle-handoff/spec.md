# Feature Specification: 会话生命周期与转人工增强

**Feature Branch**: `007-session-lifecycle-handoff`

**Created**: 2026-08-05

**Status**: Draft

**Input**: User description: "会话生命周期与转人工增强：覆盖 implementation_checklist 1.4/1.5/1.6/1.7。1.4 连续3轮未解决/累计15轮强制转人工（PRD 3.4）-- 当前转人工仅 should_interrupt 标记，缺轮次触发；session_service 无轮次计数。1.5 无坐席时留言自动建单 + 会话结束触发 CSAT 满意度评价（PRD 3.7）-- leave_message_if_no_agent 已实现未调用，CSAT 端点未接。1.6 会话30分钟空闲超时转 RESOLVED->CLOSED（PRD 3.4）-- cleanup_tasks 空文件。1.7 终端用户级限流：单用户每分钟最多10次对话（PRD 3.4/4.1）-- rate_limit_service 仅租户维度，缺用户维度。当前痛点：转人工触发条件不完整、无留言建单闭环、无 CSAT、无终端用户限流。"

## Clarifications

### Session 2026-08-05

- Q: 连续3轮"未解决"按什么标准判定？ -> A: 连续3轮可解决类意图（工单/订单/敏感操作）相同未解决，知识咨询不计入（避免正常多轮知识咨询误触转人工）。
- Q: 终端用户级限流按什么标识区分用户？ -> A: 复用现有 user_id（JWT）维度，对对话消息端点加严到 10次/分钟/用户（demo 中"终端用户"即登录用户，无独立访客身份基建）。
- Q: "无在线坐席"怎么判定？ -> A: 坐席登录态在线集合（Redis SADD on login / SREM on logout），租户在线坐席集合为空即视为无在线坐席。
- Q: 留言建单的工单如何标记为"留言"类型？ -> A: 给 Ticket 加 category 字段（nullable，默认 general；留言建单设为 message），可查询且支持未来分类。
- Q: 终端用户对话限流触发时返回什么响应？ -> A: HTTP 429 + 友好中文 detail（"您提问过于频繁，请稍后再试"），复用现有 429 模式，不落库、不计为一轮对话。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 轮次触发转人工 (Priority: P1)

当用户在同一会话连续多轮未解决问题（连续3轮意图相同未解决）或累计对话轮次过长（超过15轮）时，系统自动将 会话转接人工坐席，避免用户在 AI 循环中空耗。当前转人工仅由敏感操作/低置信度/愤怒情绪触发，缺少基于轮次的自动触发维度。

**Why this priority**：Human-in-the-loop 是宪法原则 V 不可砍链路，转人工触发条件的完整性是"企业级客服中枢"叙事的核心。轮次转人工是 PRD 3.4 明确要求且当前完全缺失的功能，优先级最高。

**Independent Test**：同一会话连续发3次相同未解决问题 -> 第3次后转人工；累计15轮 -> 转人工。

**Acceptance Scenarios**：

1. **Given** 用户连续3轮可解决类意图（工单/订单/敏感操作）相同且未解决，**When** 第3轮处理完成，**Then** 会话转人工（waiting_human），AI 不再自动接管。
2. **Given** 会话累计轮次达到15，**When** 用户发送下一条消息，**Then** 转人工。
3. **Given** 转人工后坐席处理并退回 AI，**When** 退回完成，**Then** 连续未解决轮次计数重置，重新累计。

---

### User Story 2 - 会话空闲超时自动收尾 (Priority: P1)

会话30分钟无新消息时，系统自动将 AGENT_HANDLING / WAITING_HUMAN 状态的会话转为 RESOLVED，RESOLVED 再过约定时长无操作转为 CLOSED，避免僵尸会话占用坐席队列与列表。

**Why this priority**：会话生命周期闭环影响 demo 整洁度与配额计数准确性，且实现成本低（定时任务+状态机），投入产出比高。

**Independent Test**：模拟会话最后更新时间在30分钟前 -> 定时任务将其转 RESOLVED。

**Acceptance Scenarios**：

1. **Given** AGENT_HANDLING 会话30分钟无消息，**When** 定时任务运行，**Then** 状态转 RESOLVED。
2. **Given** WAITING_HUMAN 会话30分钟无消息，**When** 定时任务运行，**Then** 状态转 RESOLVED（释放坐席认领队列）。
3. **Given** RESOLVED 会话再过约定时长无操作，**When** 定时任务运行，**Then** 转 CLOSED。

---

### User Story 3 - 无坐席留言建单 + CSAT 满意度 (Priority: P2)

转人工时若无在线坐席，用户可留言，系统自动建工单跟踪该留言；会话结束后触发 CSAT 满意度评价（1-5星 + 可选文字）。

**Why this priority**：转人工兜底（无坐席不丢用户）+ 满意度采集是客服系统标配，依赖 Story 1 转人工链路已就绪。

**Independent Test**：转人工且无在线坐席 -> 留言 -> 建单返回工单号；会话 CLOSED -> 提交 CSAT 成功。

**Acceptance Scenarios**：

1. **Given** 转人工时无在线坐席，**When** 用户留言，**Then** 系统建工单（category=message 标记为留言类型）并返回工单号。
2. **Given** 会话已 CLOSED，**When** 用户提交 CSAT 评分，**Then** 评分记录关联会话并保存。
3. **Given** 同一会话已提交 CSAT，**When** 再次提交，**Then** 拒绝（同一会话仅一次评价）。

---

### User Story 4 - 终端用户级限流 (Priority: P2)

单个终端用户每分钟最多发起10次对话消息，超出返回限流提示，防止滥用。现有 限流仅按租户维度，缺少终端用户维度。

**Why this priority**：防滥用是安全纵深一环，依赖会话消息入口已就绪，独立可测。

**Independent Test**：同一终端用户1分钟内发11条消息 -> 第11条被限流。

**Acceptance Scenarios**：

1. **Given** 终端用户1分钟内已发10条消息，**When** 发第11条，**Then** 被限流并提示稍后重试。
2. **Given** 两个不同终端用户，**When** 各自同时发消息，**Then** 互不影响（按用户维度独立计数）。
3. **Given** 限流窗口过期后，**When** 用户再发消息，**Then** 正常处理。

---

### Edge Cases

- 转人工后坐席退回 AI，连续未解决轮次计数须重置（避免退回后立即又触发转人工）。
- 空闲超时定时任务与用户恰好同时发消息的竞态：用户消息优先，定时任务跳过该会话（基于 updated_at 重新判定）。
- CSAT 在会话未 CLOSED 时提交：拒绝（仅 CLOSED 会话可评价）。
- 限流计数多实例一致性：依赖原子计数（Redis INCR），跨 worker 不串。
- 累计轮次计数在长存会话中的持久化：会话级 TTL，会话关闭时清理。
- 留言建单时若租户已禁用：拒绝并提示。
- 同一用户在多个并发会话的限流：按用户全局计数还是按会话？默认按用户全局（防滥用更严）。
- 限流触发时不落库消息记录、不计入轮次计数，仅返回 429 提示（避免限流提示污染会话）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**：系统 MUST 在会话连续3轮可解决类意图（工单/订单/敏感操作）相同未解决，或累计轮次超过15轮时，自动转人工。知识咨询类意图不计入"连续未解决"计数。
- **FR-002**：轮次计数 MUST 按会话维度记录连续未解决轮次与累计总轮次；转人工后坐席退回 AI 时 MUST 重置连续未解决计数。
- **FR-003**：系统 MUST 定时将超过30分钟无活动的 AGENT_HANDLING / WAITING_HUMAN 会话转为 RESOLVED；RESOLVED 再过约定时长无操作转为 CLOSED。
- **FR-004**：定时任务 MUST 跳过正在被用户活跃使用的会话（基于最后更新时间重新判定），避免竞态误收尾。
- **FR-005**：转人工时若无在线坐席，系统 MUST 允许终端用户留言并自动建工单跟踪，返回工单号；"在线坐席"判定基于坐席登录态（登录加入在线集合、登出移除），租户在线坐席集合为空即视为无在线坐席。
- **FR-006**：会话 CLOSED 后 MUST 支持终端用户提交 CSAT 满意度评价（1-5星 + 可选文字）；同一会话 MUST 仅允许提交一次。
- **FR-007**：系统 MUST 对单个终端用户按每分钟10次维度限流对话消息，超出 MUST 返回 HTTP 429 + 友好限流提示（不落库消息记录、不计为一轮对话）；限流键复用现有 user_id（JWT 身份），作用于对话消息端点。
- **FR-008**：终端用户限流 MUST 按 user_id 维度独立计数，MUST NOT 因某一用户触达阈值而影响其他用户；与现有租户级/用户级全局限流并存（对话端点加严到 10次/分钟）。
- **FR-009**：轮次计数与限流计数 MUST 复用现有快速存储基础设施，使用原子操作防止并发竞态。
- **FR-010**：所有自动转人工、空闲收尾、留言建单动作 MUST 携带 tenant_id 过滤（宪法原则 II），MUST NOT 跨租户收尾、计数或建单。

### Key Entities

- **会话轮次计数（SessionRounds）**：按会话维度的连续未解决轮次 + 累计总轮次，存快速存储，会话级 TTL，转人工退回时重置连续计数。
- **CSAT 评价（CsatEvaluation）**：1-5星 + 可选文字 + 会话/租户归属，模型已存在，本 feature 接入端点与校验。
- **留言工单**：转人工无坐席时的留言转工单（复用工单实体，category 字段设为 message），关联会话与租户。
- **终端用户限流状态**：按用户维度的固定窗口计数（每分钟10次），跨会话累计。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：连续3轮未解决 100% 触发转人工；累计15轮 100% 触发转人工。
- **SC-002**：30分钟无活动的 AGENT_HANDLING / WAITING_HUMAN 会话 100% 被定时任务收尾为 RESOLVED；RESOLVED 约定时长后 100% 转 CLOSED。
- **SC-003**：转人工无在线坐席时留言 100% 建单；CSAT 仅 CLOSED 会话可提交且同一会话不可重复。
- **SC-004**：单终端用户第11条/分钟 100% 被限流；不同用户互不影响。
- **SC-005**：所有轮次计数、限流、空闲收尾、留言建单操作 100% 携带 tenant_id 过滤，无跨租户影响。

## Assumptions

- CsatEvaluation 模型、human_service.leave_message_if_no_agent、rate_limit_service 已存在，本 feature 复用并接入调用，不重写。
- 定时任务在 demo 阶段用应用内后台线程（与现有 knowledge consumer 同模式），不引入 celery beat。
- "连续3轮未解决"的判定：连续3轮可解决类意图（ticket_query/order_query/sensitive_action）相同视为未解决；知识咨询（knowledge_consult）不计入，允许多轮。
- 在线坐席判定基于坐席登录态（Redis 在线集合，登录 SADD / 登出 SREM），租户在线集合为空即无在线坐席。
- 终端用户级限流复用现有 user_id（JWT 身份）维度，对对话消息端点加严到 10次/分钟（demo 中"终端用户"即登录用户，无独立访客身份基建）；限流窗口为固定1分钟滑动。
- RESOLVED 转 CLOSED 的约定时长默认30分钟（与空闲超时同窗），plan 阶段可调。
- CSAT 评价入口对终端用户开放（Widget 侧），本 feature 仅做后端端点 + 校验，前端表单不在本 feature 范围。
- 终端用户级限流按用户全局计数（跨会话累计），而非按会话，防滥用更严。