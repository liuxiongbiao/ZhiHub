# Feature Specification: LangGraph 智能体主链路骨架

**Feature Branch**: `003-langgraph-agent-chain`

**Created**: 2026-08-04

**Status**: Draft

**Input**: User description: "Day 4 — LangGraph主链路骨架：Widget发一条知识类问题，走完 路由→检索→生成→带引用回答 全链路。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — 知识问答全链路 (Priority: P1) 🎯 MVP

终端用户在 Widget 对话界面输入一条知识类问题（如"如何升级套餐"），系统经过意图路由识别为知识咨询 → 调用混合检索引擎召回相关文档片段 → 大模型基于片段生成带引用来源的回答 → 返回给用户。


**Independent Test**: Widget 输入知识类问题 → 收到带"来源：《XX文档》"引用的回答 → 打开会话详情能看到路由意图为 knowledge_consult。

**Acceptance Scenarios**:

1. **Given** Widget 已打开且创建了会话，**When** 用户输入"如何升级套餐"，**Then** 系统返回带有引用来源的回答，且意图为 knowledge_consult、置信度 > 0.5。
2. **Given** 知识库中有相关文档，**When** 用户输入语义模糊的问题，**Then** 系统仍能检索到相关片段并生成回答。
3. **Given** 用户连续对话三轮，**When** 第三轮使用指代（如"那第二种呢"），**Then** 系统能结合上下文理解意图并正确回答。

---

### User Story 2 — 意图路由分流 (Priority: P1)

系统接收到用户消息后，先由路由节点判定意图类型（知识咨询/工单查询/订单查询/敏感操作），不同意图分发到不同的处理节点。本日只实现知识咨询分支，其他意图返回"该功能即将上线"的兜底回复。

**Why this priority**: 路由是多智能体编排的入口。没有路由就没有条件分支，LangGraph 状态图就退化成了单链路。

**Independent Test**: 发送知识类问题 → 路由识别为 knowledge_consult → 进入知识 Agent；发送"我的工单进度" → 路由识别为 ticket_query → 返回兜底回复而非报错。

**Acceptance Scenarios**:

1. **Given** 会话已创建，**When** 用户输入知识类问题，**Then** 路由节点输出 intent=knowledge_consult, confidence>=0.7。
2. **Given** 会话已创建，**When** 用户输入带有强烈负面情绪的问题（如"你们太垃圾了"），**Then** 路由节点输出 sentiment=angry，系统触发人工转接提示。
3. **Given** 会话已创建，**When** 用户输入不明确的简短消息（如"帮帮我"），**Then** 路由节点输出 confidence<0.5，系统反问用户以澄清意图。

---

### User Story 3 — 会话管理 (Priority: P2)

管理员可在后台查看所有会话列表、点击进入会话详情查看消息历史和路由轨迹。会话在 30 分钟无活动后自动标记为已解决。

**Why this priority**: 会话管理是运营后台的基础能力，也是 Day 7 Trace 可视化的数据前提。

**Independent Test**: Widget 创建多个会话并发送消息 → 后台会话列表可看到所有会话 → 点击进入详情查看完整消息记录。

**Acceptance Scenarios**:

1. **Given** 存在多个不同状态的会话，**When** 管理员查看会话列表，**Then** 列表按创建时间倒序排列，支持分页。
2. **Given** 一个会话有多轮对话，**When** 管理员点击进入详情，**Then** 看到按时间排列的所有消息记录。
3. **Given** 一个会话超过 30 分钟无新消息，**When** 检测空闲超时，**Then** 会话状态自动变为 resolved。

---

### Edge Cases

- 用户发送空消息或纯空格：拒绝处理，返回提示"请输入问题"。
- LLM 调用超时或返回非 JSON 格式：路由节点降级为 knowledge_consult（默认兜底意图），confidence=0.3。
- 检索结果为空（知识库无相关内容）：Agent 回复"暂未找到相关信息，建议联系人工客服"而不是编造内容。
- 单会话消息数超过 50 轮：触发截断，保留最近 20 轮，其余归档。
- 用户快速连续发送多条消息：不排队合并，每条独立触发一次 Agent 处理（后续 Day 6 加入限流）。
- Token 预算超限：截断最早的对话轮次而非终止处理。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 能创建会话并分配唯一会话 ID，会话与租户绑定。
- **FR-002**: 系统 MUST 通过路由节点对用户消息进行意图识别，输出 intent / confidence / sentiment / slots 四个字段。
- **FR-003**: 路由节点 MUST 支持以下意图类型：knowledge_consult（知识咨询）、ticket_query（工单查询）、order_query（订单查询）、sensitive_action（敏感操作）。
- **FR-004**: 系统 MUST 在 intent=knowledge_consult 时调用混合检索引擎获取相关文档片段，并由 LLM 生成带引用来源的回答。
- **FR-005**: LLM 生成的回答末尾 MUST 包含引用来源（文档名 + 位置），片段不足以回答时 MUST 明确告知"暂未找到相关信息"而不编造。
- **FR-006**: 系统 MUST 在非 knowledge_consult 意图时返回兜底回复（如"该功能即将上线"），不报错。
- **FR-007**: 系统 MUST 提供会话列表接口（分页）和会话详情接口（含完整消息历史）。
- **FR-008**: 系统 MUST 在 30 分钟无活动后自动将会话标记为 resolved。
- **FR-009**: 系统 MUST 在单会话消息超过 50 轮或 token 数超过 4000 时截断最早的消息，保留最近上下文继续处理。
- **FR-010**: 系统 MUST 在 LLM 调用异常时返回友好的错误提示，不影响会话状态（用户可重试）。
- **FR-011**: 系统 MUST 在检测到 sentiment=angry 或 intent=sensitive_action 时触发人工转接提示。

### Key Entities

- **Session（会话）**：代表一次用户与智能体的对话。包含所属租户、当前状态、意图、置信度、情绪标签、指派坐席。状态流转：CREATED → ROUTING → AGENT_HANDLING → RESOLVED → CLOSED（Day 6 扩展 WAITING_HUMAN / HUMAN_HANDLING）。
- **Message（消息）**：会话中的一条消息。包含角色（user/assistant/agent/system）、文本内容、引用来源（JSON 数组，每项含文档名和片段位置）、元数据（如 token 数、延迟）。
- **AgentState（智能体状态）**：LangGraph 状态图中的流转数据，非持久化实体。包含会话 ID、消息历史、意图、置信度、情绪、槽位、检索结果、当前处理节点、是否应中断。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 终端用户从发送知识类问题到收到带引用回答的总延迟不超过 10 秒。
- **SC-002**: 路由节点对知识类问题的意图识别准确率 ≥ 80%（人工评估 20 条测试问题）。
- **SC-003**: 带引用回答中至少 60% 的场景能准确定位到来源文档名。
- **SC-004**: 非知识类意图（工单/订单/敏感操作）的兜底回复覆盖率达到 100%（不出现未处理异常）。
- **SC-005**: 会话列表页能展示 100 条以上会话且分页加载时间不超过 2 秒。
- **SC-006**: 会话空闲超时自动关闭在 30 分钟 ± 5 分钟内触发。

## Assumptions

- LLM（DeepSeek）API 在开发期间可用且响应正常，已配置 API key。
- Day 3 的混合检索引擎（retrieval_service.hybrid_search）已完成且可调用。
- LangGraph 1.0+ 已安装在项目依赖中。
- Redis 可用作会话上下文缓存（存储最近 N 轮对话）。
- 前端 Widget 仅做 demo 展示用途，不涉及真实嵌入第三方网站的场景。
- 本日只实现知识问答分支（router → knowledge_agent），工单/订单/转人工节点只返回占位回复，完整实现在 Day 5（MCP + 工单）和 Day 6（Human-in-the-loop）。
- 多租户隔离的 tenant_id 从 JWT 解析注入，会话和消息查询均按 tenant_id 过滤。
