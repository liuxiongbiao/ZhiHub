# Feature Specification: 技术栈对比与智能体增强

**Feature Branch**: `009-tech-compare-agent-enhance`

**Created**: 2026-08-06

**Status**: Draft


## Clarifications

### Session 2026-08-06

- Q: 长期记忆抽取什么内容、何时抽取？ -> A: 每轮 LLM 抽取关键事实（套餐/手机/偏好等结构化事实），可按用户+事实类型 upsert。
- Q: Agent 评测用哪个测试集？ -> A: 新建 ~10 条 agent 专用评测集（含预期意图+预期工具），不复用检索导向的 eval_questions.md。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Function Calling 对比实现 (Priority: P1)



**Independent Test**：切换到 Function Calling 模式 -> 查工单（含双因子）-> 返回工单详情；与 MCP 模式结果一致。

**Acceptance Scenarios**：

1. **Given** 系统切换到 Function Calling 模式，**When** 用户查工单（提供工单号+手机号），**Then** 通过 Function Calling 调用工单工具，返回工单详情，结果与 MCP 模式一致。
2. **Given** Function Calling 模式，**When** 缺手机号，**Then** Agent 反问（双因子校验逻辑与 MCP 一致）。
3. **Given** 对比文档，**When** 查看，**Then** 明确列出 MCP vs Function Calling 的差异（协议化/可审计/解耦 vs 简单直接/紧耦合）与选型理由。

---

### User Story 2 - 长期记忆 (Priority: P2)

补全跨会话长期记忆：将用户画像/历史摘要向量化存入向量库，每次对话时检索相关长期记忆注入上下文，使 Agent 跨会话记住用户偏好（如套餐、历史问题）。当前仅有 Redis 短期上下文（最近 N 轮），跨会话即遗忘。

**Why this priority**：长期记忆是智能体技术深度的体现，依赖 US1 已就绪的工具链路；比 US3 优先（影响用户体验）。

**Independent Test**：会话 A 告知偏好 -> 会话 B 问相关问题 -> Agent 调出 A 的偏好记忆。

**Acceptance Scenarios**：

1. **Given** 用户在会话 A 表明偏好（如"我是企业版套餐"），**When** 会话 A 结束，**Then** 偏好被抽取为长期记忆并存储。
2. **Given** 用户在会话 B 提相关问题，**When** Agent 处理，**Then** 检索到会话 A 的偏好记忆并注入上下文，回复体现该偏好。
3. **Given** 不相关记忆，**When** 检索，**Then** 低相关记忆不被注入（避免噪声）。

---

### User Story 3 - Agent 评测 (Priority: P3)


**Why this priority**：评测是锦上添花，依赖 US1/US2 已就绪；优先级最低（⭐ 可选）。

**Independent Test**：运行评测脚本 -> 产出意图准确率 + 工具调用成功率报告。

**Acceptance Scenarios**：

1. **Given** 评测测试集（含预期意图），**When** 运行评测，**Then** 计算意图识别准确率（正确数/总数）。
2. **Given** 评测测试集（含工具调用预期），**When** 运行，**Then** 计算工具调用成功率。
3. **Given** 评测完成，**When** 查看报告，**Then** docs/agent_eval_report.md 含两项指标数值与结论。

---

### Edge Cases

- Function Calling 模式下 LLM 不返回 tool_call（拒绝调用工具）-> 兜底回复 + 记录。
- Function Calling 与 MCP 双因子校验逻辑必须一致（复用 verify_phone_suffix，不重复实现）。
- 长期记忆抽取失败（LLM 异常）-> 跳过记忆抽取，不影响主流程。
- 长期记忆检索为空（新用户）-> 不注入记忆，正常处理。
- 长期记忆噪声（低相关）-> 相似度阈值过滤，避免注入无关记忆。
- Agent 评测中 LLM/工具失败 -> 该题标记 fail，不阻断整体，汇总含失败数。
- 切换 MCP/FC 模式不影响现有 006 安全加固（双因子/注入/熔断在 FC 模式同样生效）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**：系统 MUST 提供等价于 MCP 的 Function Calling 工具调用路径，覆盖工单查询（含双因子校验），工具分发走 LLM 原生 Function Calling 而非 MCP 协议信封。
- **FR-002**：Function Calling 路径 MUST 复用现有双因子校验、权限、熔断等安全加固（006 成果），不重复实现、不绕过。
- **FR-003**：系统 MUST 支持在 MCP 与 Function Calling 模式间切换（配置项），默认 MCP。
- **FR-004**：系统 MUST 提供对比文档，明确 MCP vs Function Calling 的差异与选型理由。
- **FR-005**：系统 MUST 提供长期记忆能力：每轮对话后用 LLM 抽取关键用户事实（套餐/手机/偏好等结构化事实）并向量化存储，跨会话保留。
- **FR-006**：每次对话处理时 MUST 检索相关长期记忆并注入上下文，使 Agent 跨会话记住用户偏好。
- **FR-007**：长期记忆检索 MUST 用相似度阈值过滤低相关记忆，避免注入噪声。
- **FR-008**：长期记忆抽取/检索失败 MUST 降级跳过，不影响对话主流程。
- **FR-009**：系统 MUST 提供 Agent 评测脚本，对新建的 agent 专用评测集（~10 条，含预期意图+预期工具）计算意图识别准确率与工具调用成功率。
- **FR-010**：Agent 评测结果 MUST 写入评测报告文档，含两项指标数值与结论。
- **FR-011**：长期记忆与评测均带 tenant_id 过滤（宪法原则 II），不跨租户记忆或评测。

### Key Entities

- **Function Calling Agent**：与 MCP ticket_agent 等价的工单 Agent，工具分发走 LLM tools 参数；复用双因子/权限/熔断。
- **长期记忆（Memory）**：用户画像/历史摘要的向量化存储，按 user_id/tenant_id 归属，含内容文本+向量+时间戳。
- **Agent 评测报告**：测试集 × {意图识别, 工具调用} 的正确率数据矩阵。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：Function Calling 模式下工单查询（含双因子）100% 返回与 MCP 模式一致的结果。
- **SC-002**：对比文档明确列出 MCP vs Function Calling ≥3 项差异 + 选型理由。
- **SC-003**：会话 A 表明的偏好，在会话 B 相关问题中 100% 被长期记忆召回并体现于回复。
- **SC-004**：Agent 评测产出意图识别准确率与工具调用成功率两项数值。
- **SC-005**：长期记忆/评测 100% 带 tenant_id 过滤，无跨租户记忆或评测。

## Assumptions

- Function Calling 通过 litellm 的 tools 参数实现（DeepSeek 兼容 OpenAI tools 格式）；与 MCP 共用同一套业务逻辑（双因子/权限/熔断），仅工具分发层不同。
- 模式切换通过配置项（如 AGENT_TOOL_MODE=mcp|fc），默认 MCP；FC 为对比/可选用。
- 长期记忆用 Milvus 新建 memory_collection（复用现有 embedding 客户端 bge-m3）；存 user_id/tenant_id/content/vector/created_at。
- 记忆抽取：每轮对话后用 LLM 抽取关键用户事实（套餐/手机/偏好等结构化事实）存入；按用户+事实类型 upsert（套餐变了覆盖旧值）。
- 记忆检索：每条用户消息向量化后检索 top-K（默认 3）相关记忆，相似度阈值过滤。
- Agent 评测测试集：新建 ~10 条 agent 专用评测集（含预期意图+预期工具），不复用检索导向的 eval_questions.md。
- 长期记忆验证需 Milvus（Docker），实现阶段不需；评测需 LLM（DeepSeek API）。
