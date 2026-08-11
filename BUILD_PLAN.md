# 智枢 ZhiHub — 10天建造计划（BUILD_PLAN）

> 本文档是你每天开工前的对照清单。项目骨架已在 `zhihub/` 目录下建好，每个源文件顶部都有注释标注：**职责 / 分工 / 对应日程**。本文档按天展开，说明当天要打开哪些文件、写什么、验收标准是什么。

## 分工图例

| 标记 | 含义 | 使用场景 |
|---|---|---|
| 🔴 | 必须强模型（Claude/GPT-4级）独立完成，不可分包 | 出错代价高：安全边界、资金逻辑、并发控制、算法核心、跨模块编排 |
| 🟡 | 弱模型/IDE起草 + 强模型 review 定稿 | 逻辑标准但有需要对齐的关键规则或安全细节 |
| 🟢 | 弱模型或 Agentic IDE（Cursor/Trae等）独立完成即可 | 模式固定、犯错影响局部、易发现易修 |

## 范围裁剪说明（对照 PRD V1.4）

本计划是 PRD V1.4 的**深度优先裁剪版**，用于10天可执行落地。裁剪原则见下表，面试被问及被砍模块时，统一话术："PRD设计阶段covered了，工程优先级上让位给了核心Agent链路"。

| 保留且做深 | 简化实现 | 直接砍掉（仅口头设计） |
|---|---|---|
| LangGraph状态机、混合检索(Milvus+ES+RRF+rerank)、MCP真实协议、Human-in-the-loop、订单/工单状态机 | 登录方式（仅手机号+密码，验证码mock）、订单状态机（核心7态，砍撤销分支）、计费（mock支付回调）、DeepAgent（最小3步骨架） | 微信登录、邮箱注册、AES字段加密、CSRF/XSS防护、RAGAS完整评测集、Langfuse自托管、Widget品牌定制、多模型计费折算、RagFlow自建（用云端embedding API替代） |

---

## Day 1 — 脚手架 + 数据模型定稿

**目标**：`docker compose up` 全部中间件健康，前端能打开空白页，后端 `/health` 返回200。

| 文件 | 分工 | 动作 |
|---|---|---|
| `zhihub/deploy/docker-compose.yml` | 🟡 | 补全前后端服务定义（此刻只需中间件能起） |
| `zhihub/backend/app/config.py` | 🔴 | 已给出骨架，核对字段是否覆盖所有中间件连接串 |
| `zhihub/backend/app/models/base.py` | 🟢 | 实现 id/created_at/updated_at 公共字段 |
| `zhihub/backend/app/models/user.py`<br>`tenant.py` `tenant_member.py` | 🟢 | 按文件头注释的字段清单写 ORM 类 |
| `zhihub/backend/alembic/env.py` + `alembic.ini` | 🟢 | 跑通 `alembic revision --autogenerate` + `alembic upgrade head` |
| `zhihub/backend/app/main.py` | 🟡 | 补 CORS，确认 `/health` 可访问 |
| `zhihub/backend/app/core/security.py` | 🔴 | bcrypt哈希 + JWT生成/解码函数写好（本日只需函数本身，接入在Day2） |
| `zhihub/frontend/`（整个脚手架） | 🟢 | `npm install` 跑通，`npm run dev` 能看到空白页 |

**验收**：7个容器 `docker compose ps` 全部 healthy；`curl localhost:8000/health` 返回 `{"status":"ok"}`；前端 `localhost:5173` 能打开。

---

## Day 2 — 账号体系 + 多租户闭环

**目标**：手机号注册 → 创建租户 → 登录 → 进后台首页，全链路打通。

| 文件 | 分工 | 动作 |
|---|---|---|
| `zhihub/backend/app/middlewares/auth_middleware.py` |  🔴| JWT解析 + Redis黑名单查询 + 注入current_user |
| `zhihub/backend/app/middlewares/tenant_middleware.py` | 🔴 | tenant_id提取注入，这是隔离防线，务必今天写对 |
| `zhihub/backend/app/api/deps.py` | 🔴 | get_current_user / require_role / require_tenant |
| `zhihub/backend/app/services/auth_service.py` | 🟡 | 注册事务编排、登录校验、密码强度检查、失败锁定 |
| `zhihub/backend/app/crud/user.py` `tenant.py` | 🟢 | 按手机号查询/创建 |
| `zhihub/backend/app/api/v1/auth.py` `tenant.py` | 🟢 | 路由粘合 |
| `zhihub/backend/app/utils/validators.py` | 🟢 | 手机号格式、密码强度规则 |
| `zhihub/frontend/src/views/login/Login.vue` `Register.vue` | 🟢 | 表单页面 |
| `zhihub/frontend/src/permission.ts` | 🟡 | 路由守卫，对齐角色体系 |
| `zhihub/frontend/src/store/modules/user.ts` `tenant.ts` | 🟢 | 状态管理 |
| `zhihub/frontend/src/api/auth.ts` `request.ts` | 🟢 | Axios封装 |
| `zhihub/frontend/src/layouts/AdminLayout.vue` | 🟢 | 基础布局 |

**验收**：注册一个手机号 → 自动创建租户(trial套餐) → 登录成功拿到JWT → 前端能进入后台首页；退出登录后旧token立即失效（黑名单生效）；坐席角色token无法调用管理员接口（本日先建好角色字段，完整拦截测试可在后续模块补充）。

---

## Day 3 — 知识库上传 + 混合检索引擎

**目标**：上传一份文档，检索测试台能看到纯向量 vs 混合检索的召回差异。

**这是全项目技术含量最高的单日**，`retrieval_service.py` 必须我全程盯。

| 文件 | 分工 | 动作 |
|---|---|---|
| `zhihub/backend/app/services/retrieval_service.py` | 🔴 | 核心算法：查询改写→并行召回→RRF融合→rerank精排→置信度判断→HyDE兜底→空知识库分支 |
| `zhihub/backend/app/utils/file_parser.py` | 🟡 | 文件校验 + 轻量切片规则（标题/段落规则，非RagFlow） |
| `zhihub/backend/app/tasks/knowledge_tasks.py` | 🟡 | 消费队列→切片→调云端embedding API→写Milvus+ES |
| `zhihub/backend/app/models/knowledge_document.py` | 🟢 | version/is_latest字段（旧版本不进检索库规则） |
| `zhihub/backend/app/crud/knowledge.py` | 🟢 | 文档元数据CRUD |
| `zhihub/backend/app/services/knowledge_service.py` | 🟡 | 上传前置校验、状态流转 |
| `zhihub/backend/app/api/v1/knowledge.py` | 🟢 | 路由粘合 |
| `zhihub/frontend/src/views/knowledge/KnowledgeList.vue` | 🟢 | 文档列表页 |
| `zhihub/frontend/src/views/knowledge/SearchTest.vue` | 🟡 | 检索测试台展示，字段对齐retrieval_service输出 |
| `zhihub/frontend/src/components/trace/RetrievalResult.vue` | 🟡 | 检索结果卡片 |

**关键配置决策**：Embedding/Rerank 用云端API（硅基流动 `bge-m3` + `bge-reranker-v2-m3`），不自建RagFlow+GPU部署，省下1-2天基础设施折腾时间。

**验收**：上传一份PDF/Word/Markdown → 后台异步处理完成状态变"已就绪" → 检索测试台输入一个精确编号类问题和一个语义模糊类问题，能看到向量召回和混合召回结果不同；空知识库时检索直接返回"未配置"提示而不触发HyDE。

---

## Day 4 — LangGraph主链路骨架

**目标**：Widget发一条知识类问题，走完 路由→检索→生成→带引用回答 全链路。

| 文件 | 分工 | 动作 |
|---|---|---|
| `zhihub/backend/app/agents/graph.py` | 🔴 | **多智能体编排核心**：State定义、节点注册、条件边、interrupt中断点 |
| `zhihub/backend/app/agents/nodes/router.py` | 🔴 | 意图识别+槙位提取+情绪判断，Prompt需反复调试 |
| `zhihub/backend/app/agents/nodes/knowledge_agent.py` | 🔴 | 查询改写+调retrieval_service+生成带引用回答 |
| `zhihub/backend/app/agents/prompts/router.txt` `knowledge.txt` | 🟢起草→🔴精调 | Prompt模板 |
| `zhihub/backend/app/services/agent_service.py` | 🔴 | 驱动状态图执行、写trace、token截断 |
| `zhihub/backend/app/services/session_service.py` | 🟡 | 会话状态机、Redis上下文、空闲超时 |
| `zhihub/backend/app/models/session.py` `message.py` | 🟡🟢 | ORM |
| `zhihub/backend/app/crud/session.py` | 🟢 | CRUD |
| `zhihub/backend/app/api/v1/session.py` | 🟢 | 路由（本日只做message发送端点） |
| `zhihub/backend/app/utils/tokenizer.py` | 🟢 | Token计数 |
| `zhihub/frontend/src/views/widget-demo/WidgetPreview.vue` | 🟡 | 对话入口页面 |
| `zhihub/frontend/src/components/chat/ChatWindow.vue` `MessageBubble.vue` | 🟡🟢 | 聊天组件 |
| `zhihub/frontend/src/types/session.d.ts` | 🟢 | 类型定义 |

**验收**：在Widget问一个知识类问题，能看到完整链路跑通并返回带"来源：《XX》"引用的回答；问一句无关话题，路由能给出合理兜底而不报错。

---

## Day 5 — MCP Server + 工单/订单Agent

**目标**：问"我的工单进度"能通过真实MCP协议查到mock数据并生成回复；创建工单重试不产生重复记录。

| 文件 | 分工 | 动作 |
|---|---|---|
| `zhihub/backend/app/mcp_servers/ticket_server.py` `order_server.py` | 🔴 | 标准MCP协议实现，工具清单：get_ticket_status/create_ticket/update_ticket/query_order/request_refund |
| `zhihub/backend/app/services/mcp_service.py` | 🔴 | 权限矩阵+幂等键+入参校验，**幂等写错=资金事故** |
| `zhihub/backend/app/services/order_service.py` | 🔴 | 状态机跃迁校验+乐观锁+退款上限校验 |
| `zhihub/backend/app/services/ticket_service.py` | 🟢 | 工单CRUD+幂等检查 |
| `zhihub/backend/app/models/ticket.py` `order.py` `refund_approval.py` | 🟢🔴🟢 | order.py状态机字段由强模型定 |
| `zhihub/backend/app/crud/ticket.py` `order.py` | 🟢🟡 | order.py乐观锁WHERE条件需review |
| `zhihub/backend/app/agents/nodes/ticket_agent.py` `order_agent.py` | 🟡 | 槙位补全循环+敏感操作判断 |
| `zhihub/backend/app/api/v1/mcp/server.py` `tools.py` | 🟡 | 挂载声明 |
| `zhihub/backend/app/api/v1/ticket.py` `order.py` | 🟢 | 路由 |
| `zhihub/backend/app/core/idempotency.py` `services/idempotency_service.py` | 🟢 | 幂等基础设施 |
| mock数据seed脚本（新建 `zhihub/backend/scripts/seed_data.py`） | 🟢 | 造测试工单/订单数据 |
| `zhihub/frontend/src/views/ticket/TicketList.vue` `order/OrderList.vue` `RefundApproval.vue` | 🟢 | 看板页面 |

**验收**：问工单进度→反问工单号（槙位不全分支）→提供工单号→查到mock数据；对同一退款请求发两次相同`client_request_id`的重试请求，只创建一条申请记录；退款金额超过订单实际支付金额被拒绝。

---

## Day 6 — Human-in-the-loop + 计费闸门

**目标**：问退款→强制转人工→坐席认领→回复；连续对话触发80%配额预警。

| 文件 | 分工 | 动作 |
|---|---|---|
| `zhihub/backend/app/core/redis.py` | 🔴 | 分布式锁完整实现（SETNX+TTL原子操作） |
| `zhihub/backend/app/services/human_service.py` | 🔴 | 坐席认领互斥锁、回退、无坐席留言建单 |
| `zhihub/backend/app/services/billing_service.py` | 🔴 | 配额计数器、超额标记、80%/100%预警、mock账单生成 |
| `zhihub/backend/app/agents/nodes/human_handoff.py` | 🟡 | 转人工条件汇总 |
| `zhihub/backend/app/middlewares/rate_limit_middleware.py`<br>`services/rate_limit_service.py` | 🟡🟢 | 双维度令牌桶 |
| `zhihub/backend/app/models/usage_record.py` `billing_invoice.py` `csat.py` | 🟢 | ORM |
| `zhihub/backend/app/tasks/billing_tasks.py` `notification_tasks.py` | 🟢 | 定时聚合、mock通知 |
| `zhihub/backend/app/api/v1/billing.py` | 🟢 | 路由 |
| `zhihub/frontend/src/layouts/AgentLayout.vue`<br>`views/agent/AgentConsole.vue` | 🟢 | 坐席工作台 |
| `zhihub/frontend/src/views/billing/UsageOverview.vue` `InvoiceList.vue` | 🟢 | 用量/账单页 |

**验收**：问"帮我申请退款"→强制中断转人工（不管Agent多有信心都不能自动处理）→坐席点击认领→两个坐席同时点击认领同一会话，只有一个成功；连续发送对话直到超过套餐配额80%，触发预警。

---

## Day 7 — 三条主链路联调 + Trace可视化

**目标**：运营后台点开任意会话，能看到完整执行轨迹（这是面试演示的重头戏）。

| 文件 | 分工 | 动作 |
|---|---|---|
| 跨模块debug（无固定文件，可能触及Day1-6任意文件） | 🔴 | 走三条主链路真实场景，修复联调发现的bug |
| MongoDB trace数据结构定稿（新建 `zhihub/backend/app/services/trace_service.py`） | 🔴 | 定义trace写入格式：意图/检索结果/工具调用/耗时 |
| `zhihub/frontend/src/components/trace/TraceTimeline.vue` | 🟡 | 时间轴UI，数据结构须对齐trace_service输出 |
| `zhihub/frontend/src/views/session/SessionDetail.vue` | 🟡 | 会话详情+trace整合页 |
| `zhihub/frontend/src/components/chat/FeedbackButtons.vue` | 🟢 | 👍/👎反馈 |
| `zhihub/frontend/src/types/trace.d.ts` | 🟡 | 类型定义 |

**验收**：三条链路（知识问答/工单查询/退款转人工）各跑一遍不报错；打开任意会话详情，能看到时间线清晰展示每一步做了什么、耗时多少。

---

## Day 8 — DeepAgent最小实现 + 效果对比材料

**目标**：能生成一份"本周工单分析报告"；有一张纯向量vs混合检索召回对比表。

| 文件 | 分工 | 动作 |
|---|---|---|
| `zhihub/backend/app/agents/deep_agent/planner.py` | 🔴 | 3步固定骨架：查询MySQL统计→检索SOP→生成报告，5次工具调用熔断 |
| `zhihub/backend/app/api/v1/agent.py` | 🟢 | DeepAgent任务发起/查询端点 |
| `zhihub/frontend/src/views/agent-report/DeepAgentReport.vue` | 🟢 | 报告发起/展示页 |
| 测试问题集（新建 `zhihub/docs/eval_questions.md`，15-20条，覆盖精确匹配/语义匹配/知识库外问题） | 🟢 | 人工整理 |
| 检索对比表（新建 `zhihub/docs/retrieval_comparison.md`） | 🟡 | 跑测试集，人工核对纯向量vs混合的召回差异并记录 |

**验收**：发起"生成本周工单积压分析报告"任务，能看到自主规划的3个中间步骤产出并汇总成报告；有一份至少15条问题的测试集和对比数据支撑"混合检索优于纯向量"的结论。

---

## Day 9 — 部署 + 安全补丁

**目标**：一条命令启动全部服务，局域网/公网可访问。

| 文件 | 分工 | 动作 |
|---|---|---|
| `zhihub/deploy/docker-compose.yml` | 🔴 | 补全backend/frontend/nginx服务，定稿 |
| `zhihub/deploy/nginx.conf` | 🟢 | 反代配置微调 |
| `zhihub/backend/Dockerfile`、新建 `zhihub/frontend/Dockerfile` | 🟢 | 多阶段构建 |
| 挑2-3个最高面试价值的安全点补上：<br>`api/deps.py`（坐席越权拦截测试）<br>`order_service.py`（退款金额上限二次确认）<br>`mcp_service.py`（幂等演示脚本） | 🔴 | 补测试用例证明生效 |
| `.github/workflows/ci.yml`（新建） | 🟢 | 基础CI：lint+跑通health check |

**验收**：`docker compose up` 一条命令启动全部服务；坐席token调用管理员专属接口（如账单查看）返回403；退款超额请求返回明确拒绝原因。

---

## Day 10 — 缓冲 + 演示脚本

**目标**：吃掉技术债，准备好一套可以流畅讲完的演示流程。

| 产出 | 分工 | 内容 |
|---|---|---|
| `zhihub/README.md` | 🔴 | 项目介绍、启动方式、架构图 |
| 演示脚本（新建 `zhihub/docs/demo_script.md`） | 🔴 | 按6个场景走一遍：知识问答/工单查询/退款转人工/坐席认领/DeepAgent报告/检索效果对比 |
| 面试追问预案（新建 `zhihub/docs/interview_qa.md`） | 🔴 | "为什么用RRF不直接加权分数"、"LangGraph和DeepAgent边界"、"多租户隔离怎么保证"等 |
| 全链路回归测试 | 🔴 | 无固定文件，实机走一遍全部演示场景 |

**验收**：从注册到DeepAgent报告的完整demo能在15分钟内无卡顿演示一遍。

---

## 进度落后时的砍法（从后往前砍，绝不动前面）

1. 先砍：Day8 DeepAgent → 做成纯mock返回一份预生成报告
2. 再砍：Day6 计费闸门细节 → 只保留配额计数，砍80%/100%预警通知
3. 再砍：Day9 安全补丁 → 只保留坐席越权拦截这一项
4. 再砍：Day7 Trace可视化前端 → 只用JSON原始展示，不做时间线UI

**绝不能砍**：LangGraph主链路(Day4)、混合检索(Day3)、MCP真实调用(Day5)、Human-in-the-loop(Day6核心)——这四个是"多智能体+RAG+MCP"简历叙事的全部证据。

---

## 每日结束前自检清单

- [ ] 今天写的 🔴 标记文件，我是否理解每一行逻辑（面试要能讲）
- [ ] 是否跑了一次端到端验收，而不是只看代码"看起来对"
- [ ] 是否有异常场景（重试/并发/超限）故意触发验证过
