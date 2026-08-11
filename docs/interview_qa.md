# 智枢 ZhiHub 面试追问预案

> 按问到的概率从高到低排列。

---

## Q1: 为什么用 RRF 融合而不是直接加权分数？

**答**: 向量相似度（内积 0~1）和 BM25 分数（无上界）分布差异大。加权融合需要归一化，归一化方案（min-max/softmax）都引入了额外假设。RRF 只依赖排序位置，天然免疫分数分布差异。k=60 是学术界和工业界验证的鲁棒默认值。

**代码位置**: `app/services/retrieval_service.py` → `rrf_fuse()`

---

## Q2: LangGraph 和 DeepAgent 的边界在哪？

**答**: LangGraph 是**固定编排**——节点和边提前定义，适合已知流程（路由→检索→回复）。DeepAgent 是**自主规划**——接收自然语言目标，自己决定执行步骤，适合开放任务（如"生成本周分析报告"）。

技术实现上 LangGraph 用 `StateGraph` + 条件边，DeepAgent 用 `execute()` 函数串联固定步骤（当前简化版）。架构预留了通用规划器扩展空间。

**代码位置**: `app/agents/graph.py` vs `app/agents/deep_agent/planner.py`

---

## Q3: 多租户隔离怎么保证？

**答**: 三道防线：
1. **中间件层**: `tenant_middleware` 从 JWT claims 解析 `tenant_id` 注入 `request.state`，不接受客户端提交
2. **数据层**: 所有 SQL 查询带 `WHERE tenant_id=?`，Milvus 用 partition key，ES 用 filter
3. **测试层**: 负向测试验证跨租户访问被拦截

**代码位置**: `app/middlewares/tenant_middleware.py` + `tests/integration/test_auth_middleware.py`

---

## Q4: MCP 协议你是怎么实现的？不是简单的函数调用吧？

**答**: JSON-RPC 2.0 标准格式。`tools/list` 返回工具定义（含 inputSchema），`tools/call` 接收 `{name, arguments}` → 权限校验 → 入参校验 → 分发到具体 Server。虽然 demo 阶段通过函数直接调用（非子进程 stdin/stdout），但协议骨架完整，面试可以讲 JSON-RPC 2.0 的请求/响应格式和 tools/list + tools/call 的标准流程。

**代码位置**: `app/mcp_servers/ticket_server.py` + `app/services/mcp_service.py`

---

## Q5: 退款怎么防止重复扣款？

**答**: 三道防线：
1. **幂等**: `client_request_id` → Redis SETNX，重复请求返回缓存结果
2. **金额校验**: `amount <= order.paid_amount`，超额拒绝
3. **状态机**: `VALID_TRANSITIONS` dict，已退款订单不可再次退款
4. **乐观锁**: `UPDATE WHERE version=expected`，并发冲突报错

**代码位置**: `app/services/order_service.py` → `request_refund()`

---

## Q6: 为什么选择 bge-m3 + bge-reranker-v2-m3？

**答**: bge-m3 是中文多语言模型，dim=1024 在精度和计算成本间折中。bge-reranker-v2-m3 是专门训练的 cross-encoder，比 bi-encoder 的向量相似度更准确。硅基流动 API 免去 GPU 部署，Demo 阶段性价比最优。

---

## Q7: Token 超预算怎么处理？

**答**: 截断而非终止。`tokenizer.estimate_tokens()` 估算总 token 数，超过 4000 预算时从最早消息开始丢弃，保留最近 10 轮。截断后继续处理而不报错。

**代码位置**: `app/utils/tokenizer.py` → `truncate_messages()`

---

## Q8: 分布式锁怎么做的？

**答**: Redis `SET key value EX 300 NX` 原子操作。`NX` 保证只有第一个 SET 成功（SET if Not eXists），后续并发 SETNX 返回 None。锁 TTL=300 秒防止死锁。认领成功后更新 DB 状态为 `human_handling`，退回时主动释放锁。

**代码位置**: `app/services/human_service.py` → `claim_session()`

---

## Q9: 这个项目的最大技术挑战是什么？

**答**: 四个挑战按难度排序：
1. **混合检索编排**（Day 3）: 查询改写→双路召回→RRF→Rerank→HyDE 兜底→空库判断，每一步的阈值和边界条件都需要仔细调试
2. **MCP 协议实现**（Day 5）: 不是简单的函数调用，要按照 JSON-RPC 2.0 标准设计 tools/list + tools/call 的完整流程
3. **资金安全防线**（Day 5-6）: 幂等+金额校验+状态机+乐观锁+强制人工，五道防线缺一不可
4. **10天时间约束下的深度优先取舍**: 砍掉了微信登录/邮箱注册/CSRF 等非核心功能，集中精力打透四条核心链路

---

## Q10: 如果给你更多时间，你会怎么改进？

**答**: 按优先级：
1. **DeepAgent 通用规划器**: 当前是 3 步固定骨架，应该用 LLM 动态分解任务+ReAct 循环
2. **完整 RAGAS 评测**: 当前有 18 条人工评测，应该跑完整的 RAGAS 指标（faithfulness/context_recall/answer_relevancy）
3. **生产级部署**: K8s + 水平扩展 + 读写分离 + 日志聚合（EFK）+ 监控（Prometheus+Grafana）
4. **安全加固**: AES 字段加密 + CSRF 防护 + SQL 注入防护 + 全链路 HTTPS

---

## MCP vs Function Calling 对比（009-tech-compare-agent-enhance）

**问: 为什么选 MCP 不用 OpenAI Function Calling？**

**答**: 两者都能让 LLM 调用外部工具，但有关键差异：

| 维度 | MCP（JSON-RPC 2.0 信封） | Function Calling（LLM 原生 tool_calls） |
|---|---|---|
| **协议** | 标准化 JSON-RPC 2.0 信封（jsonrpc/id/method/params/result/error） | LLM 厂商私有格式（tool_calls 嵌在 message 里） |
| **解耦** | 工具与 LLM 解耦：信封标准化，换 LLM 不改工具层 | 与 LLM 紧耦合：tool_calls 格式依赖 LLM 厂商 |
| **可审计** | 每次调用有标准信封，可记录 method/params/result/error_code | tool_calls 嵌在 LLM 响应里，审计需解析 LLM 输出 |
| **错误处理** | 标准错误码（-32601 方法不存在 / -32000 权限 / -32001 熔断） | 无标准错误码，各 LLM 厂商自定义 |
| **生态** | 面向多 Agent 工具市场（MCP 协议开放，工具可跨 Agent 复用） | 单 LLM 内闭环（工具定义绑定特定 LLM） |

**选型理由**: MCP 适配"多智能体 + 企业系统工具生态"场景--工具调用协议化使工具层与 LLM 解耦（换 DeepSeek/GPT/本地模型不改工具），标准信封使每次调用可审计（企业级合规），错误码标准化使熔断/权限/双因子等安全加固统一在信封层（006-mcp-security 成果）。Function Calling 更简单直接，但紧耦合 + 无标准信封，不适合多 Agent 企业级场景。

**代码证据**: `ticket_agent.py`（MCP 版）vs `ticket_agent_fc.py`（FC 版），两者业务逻辑等价（双因子/格式化一致），仅工具分发方式不同。配置项 `AGENT_TOOL_MODE=mcp|fc` 可切换。
