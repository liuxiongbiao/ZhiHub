# Research: 技术栈对比与智能体增强

> Phase 0 产出：解决 Technical Context 中的未知项与技术选型。澄清决策见 spec.md `## Clarifications`。

## R1: Function Calling 实现方式

**Decision**: 用 litellm 的 `tools` 参数（DeepSeek 兼容 OpenAI tools 格式）；ticket_agent_fc.py 定义 tools schema（get_ticket_status），LLM 决定调哪个工具，FC agent 执行工具调用并复用 006 安全加固。

**Rationale**:
- litellm.completion(tools=[...], tool_choice="auto") 让 LLM 选工具，返回 tool_calls；FC agent 解析 tool_calls 执行
- 工具执行复用 mcp_service.call_tool（含双因子/权限/熔断），仅"分发方式"不同（LLM tool_calls vs MCP 信封）
- DeepSeek 兼容 OpenAI tools 格式，无需额外适配
- FC agent 与 MCP ticket_agent 业务逻辑等价（双因子反问、结果格式化），仅工具分发层不同

- MCP：工具调用走 JSON-RPC 2.0 信封，协议化、可审计、与实现解耦
- FC：工具调用是 LLM 原生 tool_calls，简单直接但与 LLM 紧耦合、无标准协议信封

**Alternatives**:
- langchain Tool/Agent：引入 langchain 依赖，重。否决，用 litellm 原生 tools
- FC 独立实现双因子：违反 DRY + 易不一致。否决，复用 006

## R2: FC vs MCP 模式切换

**Decision**: config.py 加 `agent_tool_mode: str = "mcp"`（mcp|fc）；graph.py 按 mode 注册 ticket_agent_node 或 ticket_agent_fc_node；默认 mcp（不破坏现有）。

**Rationale**:
- 配置项切换，不改路由/端点，对调用方透明
- graph 在 build_graph 时读 mode 选节点；运行时固定（不动态切换，避免复杂）
- 默认 mcp 保现有行为；fc 为对比/可选用

**Alternatives**:
- 每请求切换（request header）：复杂、无必要。否决
- 独立端点：路由膨胀。否决

## R3: 长期记忆存储

**Decision**: Milvus 新建 memory_collection（复用 embedding_client.embed_texts + 连接管理）；schema: id/tenant_id/user_id/fact_type/content/vector/created_at。

**Rationale**:
- 复用现有 Milvus 基建（连接/embedding/索引），零新中间件
- 向量检索语义匹配（用户消息 -> 相关记忆）
- 按 tenant_id+user_id 过滤（宪法 II），fact_type 用于 upsert 去重
- IVF_FLAT + IP 索引（与 knowledge_chunks 同）

**Schema**:
- id: INT64 auto_id PK
- tenant_id: INT64（隔离）
- user_id: INT64（用户归属）
- fact_type: VARCHAR(50)（如 plan/phone/preference，upsert 键）
- content: VARCHAR(2048)（事实内容）
- vector: FLOAT_VECTOR(1024)（bge-m3）
- created_at: FLOAT（时间戳）

**Alternatives**:
- MySQL：无向量检索。否决
- Redis：不适合持久化 + 无向量检索。否决

## R4: 记忆抽取

**Decision**: 每轮 handle_message 末尾，用 LLM（DeepSeek，温度 0）从用户消息抽取关键事实 -> 结构化 [{fact_type, content}] -> embed -> upsert（按 tenant_id+user_id+fact_type，先删旧同类型再插新）。

**Rationale**:
- LLM 抽取比正则灵活（理解"我是企业版"-> {plan: 企业版}）
- 结构化 fact_type 用于 upsert（套餐变了覆盖旧值，Q1 澄清）
- 抽取失败降级跳过（FR-008），不阻断主流程
- 抽取 prompt：从用户消息提取套餐/手机/偏好等持久事实，无则返回空

**Alternatives**:
- 正则抽取：无法理解语义（"换成高级的"->升级）。否决
- 会话末摘要：记忆不及时（Q1 已否决）

## R5: 记忆检索注入

**Decision**: handle_message 开头，用户消息 embed -> Milvus memory_collection search top-K（默认 3）+ 相似度阈值（0.5）-> 命中记忆 prepend 到 context（system 角色"用户历史记忆"）。

**Rationale**:
- 每条消息检索，跨会话生效（记忆持久在 Milvus）
- 相似度阈值过滤低相关（FR-007，避免噪声）
- 注入为 system 消息，不污染 user 消息
- 检索失败降级（无记忆正常处理，FR-008）

**Alternatives**:
- 全量记忆注入：噪声大。否决
- 仅会话开始检索：会话中途偏好变化不更新。否决

## R6: Agent 评测

**Decision**: scripts/agent_eval.py，新建 ~10 条 agent 专用测试集（含预期意图+预期工具），跑 router 算意图准确率 + 跑 agent 算工具调用成功率，写 docs/agent_eval_report.md。

**Rationale**:
- 新建专用集（Q2 澄清），每条 {message, expected_intent, expected_tool}
- 意图准确率 = router intent == expected_intent 的比例
- 工具调用成功率 = agent 实际调用工具 == expected_tool 的比例
- LLM 评分有随机性，温度 0 + 多次取均值（如 008 RAGAS 同策略）
- 评测脚本独立运行，需 LLM（DeepSeek）+ 现有检索

**Alternatives**:
- 复用 eval_questions：无意图/工具标签（Q2 已否决）
- 在线评测（请求路径）：影响性能。否决，离线脚本

## R7: MCP vs FC 对比文档


**Rationale**:
- 基于代码实现（FC agent 真实存在）写对比，非空谈

**差异点**：
1. 协议：MCP 用 JSON-RPC 2.0 标准信封；FC 是 LLM 厂商私有 tool_calls 格式
2. 解耦：MCP 工具与 LLM 解耦（信封标准化，换 LLM 不改工具）；FC 与 LLM 紧耦合
3. 可审计：MCP 每次调用有标准信封可记录；FC tool_calls 嵌在 LLM 响应里，审计需解析
4. 生态：MCP 面向多 Agent 工具市场；FC 单 LLM 内闭环