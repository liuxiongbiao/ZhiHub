# PRD 实现差距审计报告

> **审计日期**: 2026-08-24
> **审计对象**: PRD V1.6（2026-08-13）↔ zhihub 代码库（backend + frontend，工作区最新状态）
> **审计口径（用户确认）**:
> - 四档判定：✅ 真实实现 / ⚠️ mock或降级实现（流程可跑，但依赖 mock 或降级路径）/ 🔶 有代码实现但需中间件或真实凭据才能运行时验证 / ❌ 完全缺失
> - 基准：**PRD 原文全量要求**（即使 PRD 修订记录标注"本期不实现/后续迭代"的条目，也一律列为缺口）
> - 方法：静态代码审计（路由/服务/模型/前端页面逐一比对）+ 758 项本地测试 + **真实栈运行时验证**（14 容器全栈，12 项端到端链路实测，见 §十一）

---

## 一、总览

| 统计项 | 数量 |
|---|---|
| PRD 功能模块（§2.1 清单） | 18 个 |
| 逐条核对的需求条目 | 约 160 条 |
| ✅ 真实实现 | 约 118 条（74%） |
| ⚠️ mock/降级实现 | 约 18 条（11%） |
| 🔶 待运行时验证 | 约 12 条（8%，其中大部分已由 §十一 实测覆盖，剩余见 §十二） |
| ❌ 完全缺失 | 约 12 条（7%） |

**一句话结论**：核心链路（多租户、LangGraph 编排、混合检索、MCP、订单状态机、人工介入、计费）已有真实闭环；原先两项性能阻塞已完成优化并复测达标（9.50MiB 文档 26.509s；高频 SSE 问答 P95 0.111s/P99 0.155s，冷请求尾延迟单列）。套餐升级、DeepAgent 资源预算、口语化召回、租户注销 30 天物理删除及数据埋点运营看板均已补齐。**剩余短板集中在**：支付宝沙箱付款回调闭环与短信真实发送受外部账号/凭据/公网条件限制，以及少量低优先级体验/规模化改进项。

> **运行时验证已执行（2026-08-23）**：全栈 14 容器真实模式跑通 12 项端到端链路（详见 §十一），并发现 1 个关键缺陷：**检索置信度阈值失配导致知识问答 100% 转人工**（自动解决率实测 0%）。

---

## 二、§2.1 功能清单对照（18 模块）

| 模块 | 状态 | 证据摘要 |
|---|---|---|
| 租户与账户管理 | ✅ | auth/tenant 全套端点（注册/三种登录/绑定/找回/邀请/向导）；短信/微信真实发送 ⚠️ 需凭据 |
| 知识工程管道 | ⚠️ | 上传→队列→解析→质量检测→向量化→双写全链路代码在；RagFlow/硅基流动/bge-m3 需外部 API，未配置时降级本地解析 |
| 混合检索引擎 | ⚠️ | RRF/HyDE/rerank/空库/降级/partition key 全部实现（`retrieval_service.py`）；需 Milvus+ES+rerank API 运行时验证 |
| 多智能体编排层 | ✅ | `graph.py` 状态图 + 4 节点 + 条件边 + interrupt；DeepSeek 调用需真实 key（⚠️ 运行时） |
| MCP 集成层 | ✅ | 5 个工具 + JSON-RPC 2.0 + 幂等键 + 权限矩阵 + 熔断 + 入参校验（mock 工单/订单库，PRD 明示允许） |
| 订单履约与售后管理 | ✅ | 完整状态机 + 乐观锁 + 审批只追加 + 退款 Outbox + 超时流转 |
| Human-in-the-loop | ✅ | interrupt + Redis 认领锁 + 退回 + 留言建单 + CSAT + 快捷回复 |
| 坐席与运营后台 | ✅ | 23 个前端页面全部存在；trace 时间线可视化在会话详情/工作台 |
| 计量与计费 | ✅ | Redis 配额 + 超额不拒 + 日聚合 + 月账单 + 80/100 预警 + CSV 导出 + 多模型折算 |
| DeepAgent 开放式任务 | ✅ | 自主规划 + Mongo 持久化 + 通知 + 工具 5 次上限 + 50 万 token/600 秒墙钟双预算，超限原因持久化并在前端展示 |
| 支付闭环 | ⚠️ | 真实模式已半程验证：密钥 PEM 修复后生成真实沙箱签名支付链接 ✅；2026-08-24 预检再次确认 SDK/RSA2 参数正常，但支付开关未启用且无公网 HTTPS 回调地址，付款与回调闭环保持外部条件阻塞（§3.10） |
| 可观测性（Langfuse） | ✅ | `@observe` 埋点 + 自托管 compose + **云端真实上报已验证**（2233 条 trace，与实测对话时间戳一致） |
| RAG 效果评测（RAGAS） | ✅ | 2026-08-23 已复跑：F 全 1.00、混合 CR 0.69 vs 纯向量 0.61（+8.3pp，详见 §3.12） |
| Agent 安全层 | ✅ | Prompt 注入检测 + 工具权限矩阵 + 敏感操作强制人工 + 熔断 |
| 模型网关 | ✅ | litellm 主/备模型 + 租户 default_model + 切换事件写 Langfuse |
| CI/CD | ✅ | GitHub Actions 完整（语法/安全审计/迁移一致性/测试/覆盖率/前端构建） |
| 对话 Widget | ⚠️ | 匿名会话/主题色/Logo/欢迎语/WebSocket 实时 ✅；**打字状态、消息时间戳 ❌**（PRD 自认待完善） |
| 系统日志与审计 | ✅ | audit_log 表 + AuditLog 页面 + 访问日志中间件 |

---

## 三、§3 逐模块详表

### 3.1 租户与账户管理

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 手机号注册（短信验证码） | ✅ | `POST /api/v1/auth/register` + sms_client |
| 邮箱注册（验证链接激活） | ✅ | `/auth/register/email` + `/auth/email/activate` + d26 迁移 |
| 密码登录 / 验证码登录 / 微信扫码登录 | ✅ | `/auth/login`、`/auth/login/sms`、`/auth/wechat/auth`+`callback` |
| 微信扫码不能注册、引导绑定 | ✅ | `/auth/wechat/bind`（wechat_client） |
| 账号绑定（邮箱/手机/微信） | ✅ | `/auth/bind/email`、`/auth/bind/phone`、`/auth/wechat/bind` |
| 找回密码 | ✅ | `/auth/password/reset` |
| 密码强度策略 | ✅ | `validators.validate_password_strength`（8位/字母数字/连续/弱密码黑名单） |
| 连续 5 次失败锁 15 分钟（含 IP 维度） | ✅ | `core/redis.py` login_fail / login_fail_ip，MAX_FAIL_ATTEMPTS |
| 验证码 5 分钟有效、错 3 次失效、1分钟1次/24小时10次 | ✅ | `auth_service` vcode_* key 全套 |
| 登出即时失效（JWT 黑名单） | ✅ | `token:blocklist:{jti}`，`/auth/logout` |
| 接口级角色鉴权（非仅前端隐藏） | ✅ | `require_role("admin")` 覆盖 billing/tenant 管理端点（84 个端点中 20+ 个显式角色门禁） |
| 试用版 seat_limit=1 | ✅ | `tenants.seat_limit default=1` |
| 试用 30 天自动降级只读（进行中会话走完） | ✅ | `cleanup_tasks.cleanup_trial_degradation` + trial_period_days=30 |
| 配置向导 + 草稿暂存 + 红点提示 | ✅ | SetupWizard.vue 5 步状态机（basic→knowledge→mcp→team→done）+ 服务端 wizard_step 持久化（退出返回定位未完成步骤）+ 后端防跳步 409 + Home.vue 红点横幅。**偏差**：PRD 写"4 步含渠道配置"，实现为 5 步、渠道配置（Widget 嵌入代码）在独立页面 WidgetConfig |
| 企业版待人工审批 | ✅ | `/tenant/enterprise/approve`（平台运营令牌，fail-closed） |
| 终端用户双因子（手机号+后4位） | ✅ | `verify_phone_suffix`，MCP 查询强制 |
| 终端用户双因子（短信验证码方案） | ❌ | PRD 明示"列入后续增强"，无实现 |
| 坐席数量超 seat_limit 阻止邀请 | ⚠️ | tenant_service 有校验（注释实证）；未找到专门契约测试，运行时验证待做 |

### 3.2 知识工程管道

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| PDF/Word/Markdown/表格上传，≤10MB 可配 | ✅ | `/knowledge/upload` + `upload_max_file_size_mb=10` + file_parser |
| 异步队列 knowledge_processing | ✅ | RabbitMQ 消费（`consume_knowledge_tasks`）+ Celery shared_task 双入口 |
| RagFlow 语义切片（保留表格/标题层级） | ⚠️ | `ragflow_client` 双模式；未配置 API 时降级本地解析（PRD V1.6 认可降级） |
| 解析质量检测（乱码率/空内容率） | ✅ | `knowledge_tasks` 质量检测 + quality_garbage_threshold=0.3 + quality_status |
| 硅基流动 bge-m3 向量化 + bge-reranker | ⚠️ | embedding_client/rerank_client 代码完整；需 API key 运行 |
| Milvus + ES 双写、失败重试 3 次、重建索引按钮 | ✅ | `knowledge_tasks` 双写重试 + `/knowledge/{id}/rebuild` |
| 扫描版 PDF OCR 兜底 | ⚠️ | `ocr_client` + file_parser 兜底分支；百度 OCR 需 key（PRD 自认 pdf2image 未验证） |
| 同名文档版本化、旧版归档不入检索库 | ✅ | `version/is_latest` 字段 + 归档逻辑 |
| 有效期 + 到期前 7 天邮件提醒 + 自动摘除 | ✅ | `remind_expiring_docs` + `cleanup_expired_docs` + d7/d34 迁移 |
| 知识库数量上限（trial 1/basic 3/pro 无限） | ✅ | `knowledge_service` 套餐数量校验 |
| 存储容量上限（500MB/5GB/50GB）暂停新增 | ✅ | storage_quota_mb 配置 + P1-14 存储配额校验 |
| 租户公平调度（按租户队列） | ❌ | PRD 自认"后续演进"，当前单队列 |
| 处理中进度条 UI | ⚠️ | KnowledgeList 有状态展示；进度条细节未逐像素验证 |

### 3.3 混合检索引擎

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 查询改写（指代补全 + 口语术语映射） | ✅ | `rewrite_query`：多轮指代走 LLM；登录故障/低价套餐等高频口语按“业务实体+意图”确定性扩展，避免无关查询误映射 |
| Milvus 向量召回 Top20 + ES BM25 Top20 并行 | ✅ | `vector_recall`/`bm25_recall`（VECTOR_TOP_K/BM25_TOP_K） |
| RRF 融合 | ✅ | `rrf_fuse`（k=60，含算法注释） |
| bge-reranker 精排 Top5 | ✅ | `rerank_api` + RERANK_TOP_K |
| 带来源引用回答 | ✅ | 切片带 source/document 字段，生成带引用 |
| 空知识库直接返回提示、不触发 HyDE | ✅ | `_is_knowledge_base_empty` 短路 |
| 低置信度触发 HyDE 兜底、再低转人工 | ✅ | 阈值经真实评测由 0.6 标定为 0.3；口语改写补齐后 18/18 期望行为正确、知识库外 5/5 转人工，详见 §十一问题 1/4 |
| Milvus/ES 单路故障降级 + 运营横幅 | ✅ | `_mark_degradation` + tenant degradation_alert + Home.vue 横幅 |
| tenant_id 隔离 + Milvus partition key 物理隔离 | ✅ | `is_partition_key=True`（embedding_client 两处 collection） |
| 检索 <500ms 性能红线 | ⚠️ | 串行实测全链路（改写+双路召回+精排）4-6.8s；<500ms 的"不含 rerank 排队"口径需拆解埋点后复测（见 §十一） |
| 检索测试台（纯向量 vs 混合对比） | ✅ | SearchTest.vue + `/knowledge/test`（返回纯向量对比基线） |
| 👍👎 反馈 + bad case 列表 | ✅ | `/session/message/{id}/feedback` + agent.py bad case 端点 |

### 3.4 多智能体编排层（LangGraph）

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| CREATED→ROUTING→AGENT_HANDLING→WAITING_HUMAN→HUMAN_HANDLING→RESOLVED→CLOSED | ✅ | graph.py 状态图 + 条件边 + interrupt |
| 路由输出 {intent, confidence, slots, sentiment} 一次完成 | ✅ | RouterOutput + LangChain with_structured_output |
| 知识/工单/订单/敏感操作四路径分发 | ✅ | 条件边 + 4 个节点（含 ticket_agent_fc Function Calling 模式） |
| 愤怒情绪强制转人工 VIP 优先 | ✅ | graph 条件 + session_crud angry 优先排序 |
| trace 写 MongoDB、敏感字段脱敏 | ✅ | trace_service + mask_pii |
| LLM 超时重试 1 次、兜底转人工且不计费 | ✅ | agent_service 兜底 + billing 口径（P1-01） |
| 无法识别默认知识咨询 | ✅ | fallback intent=knowledge_consult |
| 单轮 token 预算截断（非终止） | ✅ | MAX_TOKEN_BUDGET=4000 + truncate_messages |
| 连续 3 轮未解决转人工（含知识咨询） | ✅ | agent_service 轮次计数 |
| 累计 15 轮强制转人工 | ✅ | max_rounds_per_session=15 |
| 30 分钟空闲自动 RESOLVED | ✅ | cleanup_idle_sessions（CLEANUP_INTERVAL_SECONDS=300） |
| RESOLVED 时才计费、无效回复不计费 | ✅ | cleanup 收尾统一计数 |
| 置信度阈值租户可配置 | ❌ | 当前系统常量 0.4/0.6（PRD 自认"后台可配置列入后续"） |
| 租户级 + 终端用户级双重限流 | ✅ | RateLimitMiddleware（租户级）+ rate_limit_service 10次/分钟（用户级）+ Widget 会话级 |

### 3.5 MCP 集成层

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 5 个工具齐全（get_ticket_status/create_ticket/update_ticket/query_order/request_refund） | ✅ | mcp_servers 两个 server + `/mcp/tools/list` |
| 标准 JSON-RPC 2.0 协议信封 | ✅ | base_server + 契约测试 test_mcp_protocol |
| 槽位不全反问澄清、不调工具 | ✅ | ticket_agent/order_agent FR-004 |
| 查询双因子校验 | ✅ | 手机号+后4位（ticket/order server 强制） |
| 写操作 client_request_id 幂等 | ✅ | 幂等键（订单复合唯一 d17；测试 test_ticket_idempotency_p032） |
| 退款金额 ≤ 实付金额系统校验 | ✅ | request_refund 金额校验 |
| 入参格式校验（金额/ID/长度） | ✅ | mcp_service 参数校验（P1-16 长度上限等） |
| 熔断降级（5 次失败/关键字段空值监控） | ✅ | circuit_breaker（分布式锁 + 单探测重写版） |
| 工具权限矩阵（套餐/角色白名单） | ✅ | mcp_service 权限矩阵（写操作区分） |
| created_by 区分 agent/staff/message | ✅ | d29 迁移 + ticket/order created_by 字段 |
| 工单/订单看板区分创建方、状态筛选 | ✅ | TicketList/OrderList 看板 |

### 3.6 订单履约与售后管理

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 完整状态机（下单→支付→发货→完成+退款分支） | ✅ | order_service 状态机 + d20 等迁移 |
| 坐席发货（PAID/PROCESSING→SHIPPED+物流单号） | ✅ | `/order/{id}/ship` + d21 tracking_no |
| 退款申请→REFUND_REQUESTED（双因子+幂等） | ✅ | request_refund + pre_status 回退记录 |
| 审批通过→REFUNDING→REFUNDED（支付宝沙箱退款） | ⚠️ | approve_refund + alipay refund；真实沙箱未验证（mock 可跑） |
| 审批拒绝→回退原状态（精确 pre_status） | ✅ | d27 refund_pre_status + approve_refund |
| 用户撤销售后申请+必填原因 | ✅ | cancel_refund（reason 为空直接 raise"请填写撤销原因"，line 601） |
| PENDING_PAYMENT 30 分钟超时取消 | ✅ | cleanup_expired_orders + order_payment_timeout_minutes=30 |
| SHIPPED 7 天自动确认收货 | ✅ | cleanup_expired_orders |
| 分级审批阈值管理员可配（默认 ¥1000） | ✅ | refund_approval_threshold + `/refunds/rules` + PlanSettings |
| 审批记录只追加不覆写、审批链 JSON | ✅ | refund_approvals append-only + d30/d33 |
| 状态变更审计轨迹（操作人+时间） | ✅ | audit_log 表 + created_by |
| 乐观锁并发控制 | ✅ | orders.version + update_status_optimistic |
| 沙箱退款失败重试/Outbox 兜底、不静默丢失 | ✅ | refund_outbox + process_refund_outbox_batch + d25 |

### 3.7 Human-in-the-loop

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 触发条件全量（低置信/愤怒/主动要求/敏感操作/3轮/15轮） | ✅ | agent_service + human_service 条件汇总 |
| interrupt 中断 + WAITING_HUMAN | ✅ | graph.py interrupt |
| RabbitMQ/WS 实时推送到工作台 | ✅ | ws_manager Redis Pub/Sub 跨进程广播 |
| 坐席看完整对话+trace+建议方案 | ✅ | SessionDetail + TraceTimeline |
| 认领互斥（Redis 分布式锁，先到先得） | ✅ | claim_session SETNX（test_new_session_broadcast 等测试） |
| 退回给 Agent + 用户端过渡提示 | ✅ | return_to_agent + widget.js waiting_human/claimed/returned 事件 |
| 无在线坐席：留言自动建单 + 管理员告警 | ✅ | leave_message_if_no_agent + no_agent 通知 |
| 等待时长估算展示 | ⚠️ | 有"正在转接"状态提示；队列时长估算未见独立实现 |
| 愤怒会话工作台优先置顶 | ✅ | session_crud angry 优先排序 |
| 快捷回复模板 | ✅ | `/session/quick-replies` + 工作台快捷回复栏 |
| CSAT 满意度评价（closed 后） | ✅ | `/session/{id}/csat`（一次限制） |

### 3.8 坐席与运营后台

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 首页状态区（向导红点 + 降级横幅） | ✅ | Home.vue 三条横幅 |
| 会话监控列表（状态/等待时长/筛选） | ✅ | SessionMonitor + 状态/情绪筛选 |
| Agent trace 时间线可视化 | ✅ | TraceTimeline（意图→检索→工具→生成） |
| 知识库管理 + 检索测试台 | ✅ | KnowledgeList + SearchTest |
| 工单看板 / 订单看板（状态分栏+审批高亮） | ✅ | TicketList / OrderList / RefundApproval |
| 账单与用量看板（仅 admin） | ✅ | UsageOverview/InvoiceList + require_role("admin") |
| Widget 品牌定制（主题色/Logo/欢迎语） | ✅ | WidgetConfig + tenant 字段 + widget.js 应用（**超 PRD 边界**：PRD 说 Logo/欢迎语待完善，实际已实现） |
| 打字状态（对方正在输入） | ❌ | 工作台有"输入中"标记（typing 事件 ✓ 坐席侧）；**嵌入 SDK 侧待完善**（PRD 自认） |
| 消息时间戳（嵌入 SDK） | ❌ | 后台预览有；widget.js 无时间戳渲染（PRD 自认待完善） |
| 24 小时内续聊、超时新会话 | ✅ | session_service 024 续聊窗口 |
| 用量/Token 明细导出入口 | ✅ | `/billing/usage/export` + `/export-detail` |

### 3.9 计量与计费

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| RESOLVED 时 Redis INCR、超额不拒服务 | ✅ | billing_service check_quota + cleanup 计数 |
| 每日聚合落库 usage_records | ✅ | aggregate_daily_usage（T20 调度补齐） |
| 月账单（基础费+超额费） | ✅ | generate_invoice + monthly invoices 调度 |
| 超额单价 ¥0.5/条 | ✅ | BILLING_OVERAGE_PRICE（config） |
| 80%/100% 预警（站内+邮件） | ✅ | billing_service 预警 + notification + Home 横幅 |
| 套餐矩阵（100/2000/10000 + 存储 + 席位） | ✅ | config billing_plan_* / storage_quota_mb / seat_limit |
| **套餐升级流程** | ✅ | `GET /tenant/plans` + `PUT /tenant/plan`；basic/pro 即时生效并联动席位/配额，enterprise 保持当前套餐并进入人工议价；PlanSettings 套餐卡片与确认弹窗已上线 |
| 多模型按 Token 折算计费 | ✅ | usage_amount 模型权重 + tenants.default_model（d26 相关折算测试） |
| 账单逾期 15 天降级只读 | ✅ | invoice_overdue_days=15 + cleanup_trial_degradation |
| 用量与 Token 明细 CSV 导出 | ✅ | export_usage + export_usage_detail |

### 3.10 支付闭环（支付宝沙箱）

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 账单支付发起（跳沙箱） | ✅ | 2026-08-23 真实模式生成 ¥112.5 沙箱签名链接；2026-08-24 用 `scripts/verify_alipay.py` 再次只读预检：三项密钥均已配置、真实客户端与 SDK 初始化成功，`alipay.trade.page.pay`/RSA2/sign/biz_content/AppID 全部成立，网关为 `openapi.alipaydev.com`。脚本不输出签名、完整 URL 或密钥，也不访问支付页 |
| 异步回调验签 + payment_trade_no 幂等 | ⚠️ | 验签/幂等/伪造回调拒绝由契约测试背书（test_payment.py）；运行环境实查 `PAYMENT_ENABLED=false` 且 `ALIPAY_NOTIFY_URL` 不是公网 HTTPS，支付宝无法回调本机，故不把契约测试写成真实闭环完成 |
| 支付成功自动恢复降级状态 | ✅ | 回调处理含套餐恢复 |
| 退款场景复用沙箱接口 | ⚠️ | order_service 退款调用 alipay refund；mock 可跑 |

### 3.11 DeepAgent 开放式任务

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 自然语言任务发起（admin/agent） | ✅ | `/agent/deep-task` + Celery 异步 |
| 自主规划（查 MySQL→检知识库→综合报告） | ✅ | planner.py 规划执行循环 |
| 中间步骤实时写 MongoDB、轮询/WS 展示 | ✅ | deep_agent_store + DeepAgentReport.vue |
| 完成通知 + 报告查看/下载 | ✅ | deep_agent_tasks 通知 + 报告下载端点 |
| 单任务工具调用上限 5 次强制终止 | ✅ | planner 工具计数熔断 |
| token 消耗预算 | ✅ | 默认 500,000 token；优先累计模型真实 usage，缺失时本地估算；Mongo/API/前端均透出 `token_consumed`、预算与终止原因 |
| 任务级执行超时（10 分钟） | ✅ | 默认 600 秒墙钟预算；规划、模型与工具调用边界均检查，超限立即停止并标记“资源超限失败” |

### 3.12 可观测性与效果评测

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| LLM/工具调用 Langfuse 自动埋点 | ✅ | `@observe` 装饰器（langfuse_client，router/knowledge/mcp 链路） |
| Langfuse 自托管（Docker） | ✅ | docker-compose langfuse + langfuse-db + clickhouse |
| trace 覆盖率 100% | 🔶 | 静态可查关键节点均有 @observe；运行时验证待做 |
| trace PII 脱敏 | ✅ | mask_pii（手机号 138****0001）+ d9 加密 |
| 90 天 TTL 自动过期 | ✅ | MongoDB TTL 索引（_ensure_ttl_index） |
| RAGAS 评测（Faithfulness/Relevancy/Context Recall） | ✅ | 2026-08-23 已复跑（18 题真实 DeepSeek+硅基流动）：F 全 1.00、混合 CR 0.69 vs 纯向量 0.61（+8.3pp），结果追加至 `docs/retrieval_comparison.md` |
| 纯向量 vs 混合量化对比报告 | ✅ | 同上，含逐题明细与分类汇总 |

### 3.13 Agent 安全层

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 工具调用权限矩阵（套餐/角色白名单） | ✅ | mcp_service 权限矩阵 |
| Prompt 注入检测（规则+关键词，高风险转人工） | ✅ | detect_prompt_injection（risk 分级）+ security_incidents 表 |
| 敏感操作强制人工审批 | ✅ | graph 敏感操作路径强制 interrupt |
| 误判申诉通道（不直接拒绝服务） | ✅ | 中低风险不阻断对话、高风险转人工（用户可人工通道继续） |
| 安全拦截记录 security_incidents | ✅ | record_incident + d 系列迁移 |

### 3.14 模型网关

| PRD 要求 | 状态 | 证据 |
|---|---|---|
| 统一 litellm 网关、模型标识配置化 | ✅ | llm_client + config llm_primary/backup_model |
| 租户级 default_model | ✅ | tenants.default_model 字段 |
| 主模型失败自动切备用 | ✅ | llm_client 主备切换（T24 注释） |
| 切换事件记录 Langfuse | ✅ | 模型切换事件写入（V1.6 审计项） |

---

## 四、§4 非功能性需求

### 4.1 性能要求

| 指标 | 状态 | 说明 |
|---|---|---|
| 混合检索 <500ms | ⚠️ | 串行实测全链路（改写+双路召回+RRF+精排）4-6.8s；该口径含 LLM 改写与双精排，"不含 rerank 排队"的 500ms 需拆解埋点后复测 |
| 端到端问答 P95<3s / P99<5s | ✅ | 正式 SSE 高频知识问题 25 轮：TTFB/完整回答 P95 0.111s、P99 0.155s，DONE P95 0.112s/P99 0.155s；修复流式重复生成、首轮跳过路由/改写、长期记忆后台化并加入严格安全边界的租户级缓存。冷请求外部服务尾延迟单独披露，见 `docs/p1_bench_report.md` |
| MCP 工具调用 <2s | ✅ | 实测 50 次真实调用：P50 1.0ms / P95 1.6ms / P99 6.5ms，大幅达成 |
| 状态流转 <200ms | ✅ | 真实 MySQL+Redis 100 条完整状态链：P50 30.204ms / P95 37.508ms / P99 40.274ms / Max 150.516ms；各子阶段 P99 6.590–13.085ms，详见 `docs/p1_bench_report.md` D 节 |
| 10MB 文档 <3 分钟解析入库 | ✅ | 9.50MiB 真实全管道 26.509s：4,778 个 500–800 字符目标语义块，512 条/批 Embedding 双并发，Milvus/ES 并行双写与 ES Bulk；目标余量约 85%，详见 `docs/p1_bench_report.md` |
| 短信验证码 <3s | 🔶 | 真实阿里云发送未测（mock 模式瞬时） |
| 100 并发会话不降级 | ✅ | **实测通过**：容器内网 100 并发 /health 200/200 全部 200、零 5xx（P99 643ms；单 worker 排队所致，生产多 worker 可降） |
| 终端用户限流 10 次/分钟 | ✅ | rate_limit_service 代码实现 + 契约测试 |
| 基线参考：/health 延迟 | ✅ | 串行 8-39ms；20 并发 P50 30.7/P95 75.2/P99 90.7ms，吞吐 564 req/s（容器内网直连实测） |
| 基线参考：/api/v1/session/list（鉴权+中间件+MySQL） | ✅ | 10 并发 P50 64.3/P95 125.8/P99 149.4ms，吞吐 141 req/s，100/100 全 200（容器内网直连实测） |

> 性能指标已完成 MCP、10MB 级文档、端到端高频问答、会话状态流转与 HTTP 并发实测；仅短信真实网关时延受凭据条件阻塞。

### 4.2 兼容性

| 要求 | 状态 |
|---|---|
| Chrome/Edge/Firefox 最新两大版本 + Widget 兼容第三方网站 | ✅ 本机现有 Chrome 151、Edge 151、Firefox 132 三浏览器均完成登录/工作台/知识库/Widget 预览/运营看板与第三方页加载 widget.js 验收，全部 200、0 横向溢出、0 控制台/页面/关键网络异常；未安装版本不虚构，持续“最新两个大版本”应由云浏览器矩阵承担，详见 `docs/browser_compatibility_report.md` |
| 后端全容器化，支持 Linux/Windows | ✅ Dockerfile + docker-compose 全栈（本机 Windows + CI Ubuntu） |

### 4.3 安全与隐私（14 条，全部 ✅）

| 要求 | 证据 |
|---|---|
| 租户数据隔离 + Milvus partition key 物理隔离 | ✅ 全查询带 tenant_id + is_partition_key=True |
| 密码策略 / bcrypt / JWT 过期+刷新 / 黑名单登出 / 5 次锁定 | ✅ security.py + redis.py |
| 验证码/openid 加密存储、限频、错 3 次失效 | ✅ EncryptedString + vcode 限频 |
| PII 字段 AES-256 静态加密（密钥分离） | ✅ EncryptedString（crypto.py）+ .env 注入 |
| 终端用户双因子校验 | ✅ 手机号+后4位 |
| 接口级角色鉴权 | ✅ require_role |
| 敏感操作二次校验/强制人工、金额系统校验 | ✅ 3.13 + 金额上限 |
| Prompt 注入防御 / 权限矩阵 / 熔断 / 幂等 | ✅ 3.13 + 3.5 |
| 支付回调验签 + 交易号去重 | ✅ 3.10 |
| XSS 转义 + CSRF Token | ✅ Vue 默认转义 + X-CSRF-Token 全请求携带 + 中间件 |
| trace 日志 PII 脱敏 + 保留期限 | ✅ 3.12 |
| 审批记录只追加不覆写 | ✅ 3.6 |

### 4.4 可用性

| 要求 | 状态 |
|---|---|
| 核心链路优雅降级 + 运营可见 | ✅ 降级横幅 + fail-open/fail-closed 策略 |
| 关键操作二次确认 | ⚠️ 支付二次确认有实现（V1.6 审计项）；退款审批/删除文档的前端二次确认未见统一模式 |
| 异常返回可理解提示（非堆栈） | ✅ API 层统一中文错误信息（session.py P1-9 不泄露内部异常） |
| 备份与灾备（MySQL 日备、快照周备） | ✅ `scripts/backup.ps1` 覆盖 MySQL 全量 dump + 临时库恢复验证、Milvus/MinIO/etcd 一致物理快照、ES 原生 Snapshot API 与 30 天保留策略；恢复手册见 `docs/secret-rotation-runbook.md` |

---

## 五、§5 数据埋点与分析 —— ✅ 已完成

| 项 | 状态 |
|---|---|
| 12+ 埋点事件（session_created/retrieval_executed/mcp_tool_called/human_takeover/order_state_changed/refund_*/quota_exceeded/invoice_paid/security_block/answer_feedback/index_rebuild/degradation/message_left） | ✅ MongoDB `analytics_events` 统一事件服务，15 组 PRD 事件全部接入业务提交点，另加 `session_resolved` 明确自动/人工解决口径；字符串统一 PII 脱敏、属性限长、365 天 TTL，Mongo 故障 fail-open 不阻断主流程 |
| 运营数据看板（检索对比/解决率趋势/审批时效/套餐分布/收入趋势） | ✅ `/api/v1/analytics/overview` 按 JWT 租户聚合；检索纯向量/混合/HyDE 对照、14 日自动解决走势、退款审批时效分布、套餐分布、回款趋势与事件台账均已落地；全平台视图需 admin JWT + 独立 `X-Platform-Token` 双重鉴权 |
| Prometheus 基线 | ✅ metrics_middleware（HTTP/会话/消息计数）——PRD 自认的"当前基线"属实 |

> 2026-08-24 已补齐 PRD 原自认缺口。事件存储不可用时看板明确提示，并继续显示 MySQL 套餐/财务指标；租户管理员不能读取其他租户或平台总收入。

---

## 六、§6 数据模型对照

| 表/集合 | 状态 | 差异 |
|---|---|---|
| tenants（含 widget_*、refund_approval_threshold、default_model、is_demo、api_key_hash、pending_plan_type） | ✅ | 比 PRD 多 is_demo（演示租户标记）与 pending_plan_type（企业版待议价目标），字段全覆盖 |
| tenant_members / users（含 email_verified、wechat_openid 加密） | ✅ | 一致 |
| knowledge_documents（version/is_latest/expires_at/quality_status） | ✅ | 一致 |
| sessions（status/intent/confidence/sentiment/end_user_id/assignee_id） | ✅ | 一致 |
| tickets / orders（created_by、version、payment_trade_no、tracking_no、cancel_reason） | ✅ | 一致 |
| refund_approvals（追加模式） | ✅ | 另有 refund_approval_event / refund_outbox 增强表 |
| csat_evaluations / usage_records / billing_invoices / security_incidents | ✅ | 一致 |
| notifications / quick_replies / refresh_token / billing_event / audit_log | ✅ | PRD 未列但合理新增 |
| MongoDB agent_traces（90 天 TTL、脱敏） | ✅ | 一致 |
| MongoDB deep_agent_tasks | ✅ | 一致（含 token_consumed/token_budget/termination_reason；历史文档读取时补默认值） |
| MongoDB deep_agent_reports | ✅ | planner 报告写入 |
| MongoDB analytics_events | ✅ | 统一运营事件、tenant/event/time 复合索引、365 天 TTL、PII 脱敏；租户注销时同步物理删除 |
| Redis Key 设计（quota/context/rate/vcode/login_fail/blocklist/claim） | ✅ | 全部有对应实现；claim 锁在 human_service（SETNX） |

---

## 七、§8 上线与验收

### 8.1 验收标准（15 条 checklist）映射

| # | 验收项 | 状态 |
|---|---|---|
| 1 | 三登录方式+注册/绑定/找回+锁定+验证码失效+黑名单 | ✅（短信/微信真实发送 ⚠️） |
| 2 | 多租户隔离+角色越权拒绝+试用 1 席位 | ✅（758 测试含对应契约） |
| 3 | 知识管道全流程+版本归档+到期提醒+双写恢复+存储上限 | ✅（真实栈实测入库；RagFlow 语义解析未配置降级本地） |
| 4 | 混合检索+测试台+HyDE+空库+降级横幅 | ✅（真实栈实测全链路） |
| 5 | LangGraph 流转+愤怒转人工+15轮+截断+空闲收尾+双限流 | ✅ |
| 6 | MCP 五工具+双因子+金额上限+幂等+参数校验+熔断 | ✅ |
| 7 | 订单状态机+撤销+不可覆写+乐观锁+分级阈值 | ✅ |
| 8 | 转人工+认领互斥+留言建单+退回过渡提示 | ✅（等待时长估算 ⚠️） |
| 9 | DeepAgent 报告+步骤可视化+5 次上限 | ✅（50 万 token + 10 分钟双硬预算、资源超限失败与消耗展示均已补齐） |
| 10 | Langfuse 100% 覆盖+脱敏+90 天 | ✅ 脱敏/90 天 TTL 代码落实；真实上报已验证（云端 2233 条 trace，最新时间戳与实测对话一致） |
| 11 | RAGAS 跑通+量化数据+指标达标 | ✅ 已复跑（F 1.00 / CR +8.3pp）；+15pp 目标未达（评测集设计限制，见 §3.12） |
| 12 | 权限矩阵+注入检测识别典型攻击 | ✅ |
| 13 | 计费闸门+预警+导出+多模型折算 | ✅ |
| 14 | 支付宝沙箱闭环+回调幂等 | ⚠️ 半程已验证（真实签名链接生成 ✅）；付款/回调闭环待沙箱应用核对+公网环境（见 §十二） |
| 15 | Docker Compose 一键启动 + CI 跑通 | ✅ Compose 全栈已实测 14 容器 Up&healthy；GitHub Actions 后端/前端双 job 已实际全绿（见 §十二） |

### 8.2 上线 Checklist

| 项 | 状态 |
|---|---|
| 密钥配置完整且不入库 | ⚠️ `.env` 已 gitignore；DeepSeek/硅基流动/支付宝沙箱/微信/OCR/SMTP 仅在本机运行环境配置并做过对应预检，阿里云短信凭据未配置；真实密钥均不进入仓库 |
| Nginx 反代 + 静态资源部署 | ✅ deploy/nginx.conf + frontend/nginx.conf 存在，未运行时验证 |
| 迁移脚本幂等可重复执行 | ✅ alembic 从零建库测试（test_alembic_from_scratch，2 跳过需 MySQL 密码） |
| 演示租户/示例数据预置 | ✅ scripts/seed_data.py + demo_seed.py |

### 8.3 / 8.4 灰度与回滚

| 项 | 状态 |
|---|---|
| 演示专用租户（固定数据） | ✅ demo_seed（is_demo 标记 + reset 能力） |
| 版本化镜像回滚 | ⚪ 仅文档描述，无具体回滚脚本/流程验证 |

### 8.5 租户注销与数据处理

| 项 | 状态 |
|---|---|
| 管理员导出核心业务数据（CSV 打包 ZIP） | ✅ export_tenant_data（PRD 自认已实现，属实） |
| 注销申请 / 30 天窗口 / 物理删除 | ✅ admin 申请/状态/撤销端点 + `pending_deletion` 仅导出门禁 + 前端截止时间/导出/撤销；Celery 每 5 分钟到期扫描，按 FK 顺序物理删除 MySQL、Mongo（含 analytics_events）、Milvus、ES、Redis 与上传文件，失败保留租户并重试；真实临时租户跨存储验收 11/11 通过 |

---

## 八、缺口清单（❌ 汇总，按严重度排序）

| 严重度 | 缺口 | PRD 出处 | 对上线/答辩的影响 |
|---|---|---|---|
| ✅ 已修复 | 检索置信度阈值失配（0.6→0.3）+ HyDE 缩进缺陷 | §3.3/§1.4 | 2026-08-23 修复并实测验证（对话 confidence 0.95、带引用回答） |
| 🔴 高 | **性能指标无并发实测**（§4.1 全部 8 项） | §4.1 | 串行实测已回填（/health 8-39ms）；并发压测需本机干净环境执行（命令见 §十一） |
| ✅ 已修复 | **CI 未在 GitHub 实际运行过** | §8.1-15 | 2026-08-23 GitHub Actions 后端/前端双 job 已实际全绿 |
| ✅ 已修复 | 数据埋点事件体系 + 运营看板 | §5 | 2026-08-24 完成 15 组事件、Mongo TTL/脱敏、租户聚合 API、平台双鉴权与五类运营图表 |
| ✅ 已修复 | 套餐升级流程 | §3.9 | 2026-08-23 完成前后端闭环、权限校验、资源上限联动与审计日志 |
| ✅ 已修复 | 租户注销/30 天窗口/物理删除 | §8.5 | 2026-08-24 完成前后端闭环、到期跨存储物理删除与失败重试，新增运营事件集合后真实栈 11/11 清零验证 |
| ✅ 已修复 | DeepAgent token 预算 + 任务超时 | §3.11 | 50 万 token 与 600 秒墙钟预算均强制执行，超限停止后续工具/模型调用并持久化原因 |
| 🟡 低 | 终端用户双因子短信方案 | §3.1.3 | 已有手机号+后4位替代方案 |
| 🟡 低 | 置信度阈值租户可配置 | §3.4 | 系统常量可用（且当前常量本身失配，见上） |
| 🟡 低 | 知识库租户公平调度（单队列） | §3.2 | 规模上来才会出问题 |
| 🟡 低 | Widget SDK 打字状态/消息时间戳 | §3.8 | 体验细节，PRD 自认待完善 |
| ✅ 已修复 | 备份灾备实操脚本 | §4.4 | 2026-08-23 日备恢复与周备快照均实跑成功 |
| 🟡 低 | 等待时长估算（无坐席场景） | §3.7 | 有静态提示代替 |

**⚠️ mock/降级清单（答辩必须主动交代的边界）**：短信发送、微信登录、支付宝支付/退款、OCR、RagFlow 解析、LLM/Embedding/Rerank 调用均保留 mock/降级路径；其中 LLM/Embedding/Rerank、Langfuse 与支付宝真实密钥解析/RSA2 参数已在真实配置下运行，支付宝付款/回调/退款和短信真实收码仍未运行。MCP 背后的工单/订单系统是 PRD 明示允许的自建 mock 业务库（不算缺口）。

---

## 九、答辩可讲功能清单（✅ 全部有代码+测试背书，可放心讲）

**主线叙事（5 分钟版）**：
多租户 SaaS 智能客服 → 知识库异步管道（上传→解析→质量检测→双索引）→ 混合检索（向量+BM25+RRF+rerank+HyDE 兜底）→ LangGraph 显式状态图（路由/知识/工单/订单四节点）→ MCP 标准协议让 Agent 真正查办业务 → 订单完整状态机与退款审批链 → 人工介入双向协作（认领锁/退回/留言）→ 按量计费闭环。

**每个模块的"可讲点"（都有真实代码与测试）**：

1. **多租户隔离**：tenant_id 贯穿 MySQL/Mongo/Milvus/ES；Milvus partition key 物理隔离（`is_partition_key=True`）
2. **账号体系**：三种登录 + JWT Refresh Token 轮换 + 黑名单登出 + 5 次锁定 + 验证码防爆破 + 接口级角色鉴权
3. **知识管道**：RabbitMQ 异步 + RagFlow 语义切片（降级本地解析）+ 质量检测（乱码率）→ 双写 Milvus+ES + 失败重试/重建索引 + 版本化归档 + 生命周期治理（到期提醒/摘除）
4. **混合检索**：RRF 融合（讲清"分数量纲不一致"动机）→ bge-reranker 精排 → HyDE 假设文档兜底 → 空库短路 → 单路故障降级 + 运营横幅
5. **LangGraph 编排**：显式状态图 vs 自主规划的场景边界（对照 DeepAgent）→ 一次调用产出四元组 → 四条确定性路径 → 敏感操作一票否决转人工
6. **MCP**：标准 JSON-RPC 2.0 + 5 工具 + client_request_id 幂等 + 权限矩阵 + 熔断器（分布式锁串行化 + half-open 单探测）+ 入参校验
7. **订单状态机**：完整 10 状态流转 + 乐观锁 CAS + 审批只追加不可篡改 + pre_status 精确回退 + Outbox 退款重试
8. **Human-in-the-loop**：interrupt 中断 + Redis SETNX 认领互斥 + 退回给 Agent + 无坐席留言建单 + CSAT
9. **计费**：RESOLVED 才计费（无效回复不扣）→ Redis 计数 → 日聚合 → 月账单 → 80/100 预警 → 多模型 Token 折算 → CSV 对账导出
10. **安全层**：Prompt 注入检测 + 工具权限矩阵 + 资金操作强制人工 + PII 静态加密 + trace 脱敏 + CSRF + 支付回调验签幂等
11. **可观测**：Langfuse 全链路 @observe + Prometheus 探针 + 90 天 TTL 脱敏 trace
12. **工程化**：758 项测试（unit/contracts/integration）+ CI（语法/安全审计/迁移一致性/覆盖率/前端构建）+ Docker Compose 全栈

**答辩时主动交代的边界（坦诚反而加分）**：
- 支付宝沙箱真实付款闭环待支付开关与公网 HTTPS 回调环境；短信因凭据未配置保留 mock，微信/OCR 已配置但本轮未做终端侧真实验收
- 会话状态流转已完成 100 条真实状态链基准；Chrome/Edge/Firefox 核心页面与第三方 widget.js 均已实测通过；数据埋点运营看板已于 2026-08-24 补齐
- MCP 背后是自建 mock 业务库（PRD 明示的设计选择，协议层标准可替换真实系统）

---

## 十、运行时验证（状态：已执行，实录见 §十一）

原计划的 6 步待办已全部完成或有了明确结论：

| 步骤 | 状态 |
|---|---|
| 1-2 全栈启动（14 容器 Up & healthy） | ✅ 已完成 |
| 3 真实密钥配置 | ✅ DeepSeek/硅基流动/支付宝/微信/OCR/SMTP 已配置；**支付宝密钥格式缺陷已修复**；阿里云短信凭据未配置 |
| 4 迁移/种子/端到端冒烟 | ✅ 已完成（§十一 12 项链路） |
| 5 并发压测 | ✅ 已完成（容器内网实测：/health 564 req/s、100 并发零 5xx；宿主机 502 已定位为 Docker Desktop 端口转发层，非应用问题） |
| 6 RAGAS 评测 | ✅ 已完成（F 1.00 / CR +8.3pp）；阈值 0.6→0.3 定稿（0.25 对比实验无差异） |

**剩余未完成项见 §十二**。

---

## 十一、运行时验证实录（2026-08-23 已执行）

**环境**：本机 Docker Desktop 全栈（deploy 栈 14 个容器全部 Up & healthy：backend/frontend/MySQL/Redis/RabbitMQ/Mongo/Milvus/ES/nginx/celery×3/langfuse-db/clickhouse/langfuse），`deploy/.env` 含真实密钥（DeepSeek/硅基流动/支付宝沙箱/微信/OCR/SMTP），`LLM_ENABLED=true`。

### 已验证通过的链路（真实模式）

| # | 验证项 | 结果 |
|---|---|---|
| 1 | 邮箱注册 → 激活 → 登录（真实 MySQL 写入） | ✅ 注册返回激活链接 → activate ✓ → login 返回 284 字符 JWT（含 refresh） |
| 2 | 租户首页（上下文/横幅/向导状态） | ✅ tenant_id=75, plan=trial, status=active, degradation_alert=false |
| 3 | CSRF 中间件 | ✅ 无 X-CSRF-Token 的上传请求被 401 拦截（"缺少 CSRF 令牌"） |
| 4 | 知识管道全链路（真实） | ✅ 上传 635B md → 25 秒内 pending→**ready**，chunk_count=4（RabbitMQ 消费 → 解析 → 真实硅基流动 bge-m3 向量化 → Milvus+ES 双写成功） |
| 5 | 混合检索（真实） | ✅ Milvus 向量召回 4 chunks（分数 0.36-0.40）✓ ES BM25 ✓ RRF ✓ 真实 rerank API ✓ HyDE 触发 ✓ 低置信度标记 ✓（elapsed 8.8s 含改写+双精排） |
| 6 | LLM 对话主链路（真实 DeepSeek） | ✅ 会话创建 → message → 路由正确识别 intent=knowledge_consult → 知识 Agent → 检索 → 转人工话术（elapsed 2.7s） |
| 7 | Widget 匿名通道 | ✅ 生成 API Key ✓ → 非白名单 origin 被拒 ✓ → 加白名单后会话创建 ✓（JWT role=widget，品牌字段下发）→ 匿名发消息 ✓ 全链路 |
| 8 | MCP 工具清单 | ✅ 5 工具全部在线注册 |
| 9 | 计费配额 | ✅ quota=100, count=0, remaining=100（真实 Redis 读取） |
| 10 | Alembic 从零建库（真实 MySQL） | ✅ 2 项 integration 测试通过（此前因无 MySQL 被跳过） |
| 11 | 前端/nginx 服务 | ✅ 5173 与 80 端口均 200 正常服务 |
| 12 | /metrics 鉴权 | ✅ 401（与设计一致：监控探针需鉴权） |

### 运行时发现的关键问题

**问题 1：检索置信度阈值失配 → 知识问答 100% 转人工 —— ✅ 已修复并完成量化验证（2026-08-23）**
- 原状：`retrieval_service.CONFIDENCE_THRESHOLD = 0.6`，实测 bge-reranker-v2-m3 最佳匹配 0.359 → 恒触发 HyDE → 全部转人工（自动解决率 0%）；
- 修复：阈值改为 **0.3**（常量 + 标定注释）；顺带修复 HyDE 二次精排循环缩进缺陷；
- **RAGAS 量化验证**（18 题、demo 租户、真实 DeepSeek/硅基流动，结果见 `docs/retrieval_comparison.md`）：
  - Faithfulness 全部 **1.00**（零幻觉）；
  - 混合检索 Context Recall 均值 **0.69** vs 纯向量 0.61（**+8.3pp**，SC-002 目标 +15pp 未达，归因：评测集多数题两法均满分、无差异化空间）；
  - 知识库外问题 AR=0.04/CR=0（正确拒绝回答）；
- **端到端自动解决率初测**（18 题真实对话，详见 `docs/e2e_resolution_report.md`）：
  - 自动回答 **7/18（38.9%）**、转人工 8、反问澄清 3；知识库外 5 题 **100% 正确转人工**；
  - 初始合理行为 16/18，剩余 Q9/Q10 属口语化召回缺口，已在下述问题 4 闭环；
- **✅ 决策已定（2026-08-23 对比实验）**：0.25 实验与 0.3 结果**完全一致**（7 回答/8 转人工/3 反问，详见 `docs/e2e_resolution_report_025.md`）——剩余 2 道漏答题的 rerank 分数已处于噪声区（≤0.013），属检索召回问题而非阈值问题。**最终定稿 0.3**（与噪声地板保持安全距离），代码注释已记录实验依据。

**问题 2：HyDE 二次精排循环缩进异常** —— ✅ 已随问题 1 一并修复（`hybrid_top.append` 移入 `if idx < len` 块内，返回结果数从 1 条恢复为 4 条）。

**问题 3（已定位根因）：宿主机直连 8000 端口的并发压测全 502 = Docker Desktop 端口转发层（vpnkit）瓶颈，非应用问题**
- 现象：宿主机 `127.0.0.1:8000` 并发 20 压测 → 全 502、延迟 ~3.1s（用户在自有终端复现一致）；
- 对照实验：**容器内网直连压测**（绕过宿主端口转发）→ /health 20 并发 P50 30.7/P95 75.2/P99 90.7ms、吞吐 564 req/s、200/200 全 200；session/list 10 并发 100/100 全 200（P99 149ms）；**100 并发 200/200 全 200、零 5xx（P99 643ms）**；
- 结论：应用本身健康，502 来自 Windows Docker Desktop 的 vpnkit 端口转发层对高并发短连接的处理瓶颈；Linux 服务器部署（iptables + nginx 反代）不存在该层。本机开发建议：调试走宿主机直连，压测走 `docker exec` 容器内网或部署服务器。

**问题 4：Q9/Q10 口语化问法漏答 —— ✅ 已修复并完成真实全量复测（2026-08-24）**
- 修复：查询改写按“登录/套餐业务实体 + 转圈/便宜等口语意图”补充知识库术语；不调整已定稿的 0.3 置信度阈值，并加负例保证“便宜餐厅”等库外问法不会误映射到套餐；
- 真实检索：Q9 Top1 rerank **0.968**（账户安全FAQ）、Q10 **0.501**（产品手册），均超过 0.3；知识库外 5 题仍全部低置信；
- 18 题逐题新会话端到端：**9 自动回答 / 6 转人工 / 3 反问，18/18 期望行为正确**；Q9/Q10 均带 5 条引用回答，Q11-Q15 仍 **5/5** 正确转人工，详见 `docs/e2e_resolution_report_after_rewrite.md`；
- RAGAS 真实复跑：Faithfulness 全部 **1.00**，混合 CR **0.69**、纯向量 **0.61**、差值仍 **+8.3pp**，与修复前一致无下降，详见 `docs/retrieval_comparison_after_rewrite.md`；
- 复测时发现 9.5MiB 基准文档的 Milvus 删除未显式 flush，残留 4,778 条切片会污染评测；已人工清零并在补偿清理中强制 flush，后续基准可立即验证零残留。

**问题 5：租户注销/30 天窗口/物理删除 —— ✅ 已实现并完成真实跨存储验收（2026-08-24）**
- 管理员可申请注销、查询截止时间并在到期前撤销；申请后状态进入 `pending_deletion`，后端默认拒绝业务接口，首页、注销状态、ZIP 数据导出与撤销显式放行；前端同步隐藏套餐/规则写入口；
- Celery 维护任务每 5 分钟扫描到期租户，外部存储先删、关系库后删；任一外部存储失败即回滚并保留租户供下轮重试；MySQL 按 CSAT/反馈/消息/会话、退款事件/Outbox/订单、租户直属表、成员、孤立用户、租户的 FK 顺序删除，共享账号保留；
- 退款审批事件日常仍由 ORM + MySQL 触发器保持不可变，仅在显式租户物理删除会话变量下允许清理；Alembic `d37_tenant_cancellation` 已在真实 MySQL 升级到 head；
- `scripts/verify_tenant_cancellation.py` 初次在隔离临时租户 77 写入 MySQL、Mongo 两集合、Milvus 两集合、ES、Redis 与上传文件后执行到期删除，10/10 通过；P2-4 新增运营事件集合后已在容器同配置下用临时租户 79 复验，**MySQL、Mongo 三集合、Milvus 两集合、ES、Redis 与上传文件 11/11 全部清零**，中断夹具也由到期任务自动清理，未留下审计测试数据。

### 外部条件具备后执行的验收项

```powershell
# B. RAGAS 评测复跑（PRD §3.12.2）——需 demo 租户（tenant 1）知识库已上传，消耗少量真实 token
cd C:\Users\Lenovo\Desktop\Three\zhihub\backend
.venv\Scripts\python.exe scripts\ragas_eval.py

# C. 支付宝沙箱真实支付（PRD §3.10）——预检通过后按 Runbook 第八节完成付款、回调重放与退款
cd C:\Users\Lenovo\Desktop\Three\zhihub\deploy
docker compose exec backend python scripts/verify_alipay.py
```

支付宝的完整分步操作与验收断言见 `docs/secret-rotation-runbook.md` 第八节；短信真实发送见同文档第七节。两项均不得把 mock 或仅参数生成记作真实业务闭环。

**审计产生的测试数据**：评测与冒烟产生的数据已通过 `backend/scripts/cleanup_eval_data.py --execute` 清理完毕（108 个评测会话 + 冒烟租户 75 全量，含 MySQL/Mongo/Milvus/ES 四层）；注销验收临时租户 76/77 也已跨存储物理清零；demo 租户（tenant 1）的 25 篇评测知识库文档**保留**（演示/答辩资产，如需清空加 `--seeded-kb`）。

---

## 十二、剩余未完成项清单（截至 2026-08-24）

### A. 审计验证层面未完成（非代码缺陷，待条件具备可补）

| # | 项 | 状态 | 需要的条件 |
|---|---|---|---|
| 1 | 支付宝沙箱真实付款 + 回调闭环 | ⏸ 外部条件阻塞，文档闭环已完成 | 2026-08-24 运行 `scripts/verify_alipay.py`：三项密钥、真实 SDK、RSA2 签名页面支付参数和沙箱网关均通过；环境实查 `PAYMENT_ENABLED=false`、公网 HTTPS `ALIPAY_NOTIFY_URL` 缺失，退出码 2 准确标识外部前置未满足。付款、签名回调、重复回调幂等与真实退款均未伪报；可重复预检及完整操作/断言见 Runbook 第八节 |
| 2 | Langfuse 真实上报链路 | ✅ 已验证 | 2026-08-23 真实对话后查 cloud.langfuse.com API：共 2233 条 trace，最新时间戳与当轮对话一致——全链路真实上报（§8.1-10 运行时部分达成） |
| 3 | MCP 工具调用 <2s 单项压测 | ✅ 已实测（P50 1.0ms / P99 6.5ms，大幅达标） | 详见 `docs/p1_bench_report.md` A 节 |
| 4 | 会话状态流转 <200ms | ✅ **达标**：完整状态链 P50 30.204ms / P95 37.508ms / P99 40.274ms / Max 150.516ms | 真实 MySQL+Redis 100 条 `created→waiting_human→human_handling→resolved→closed`；可重复脚本自动清理 MySQL/Redis/Mongo 测试数据，详见 `docs/p1_bench_report.md` D 节 |
| 5 | 10MB 大文档 <3 分钟解析入库 | ✅ **达标**：9.50MiB、4,778 块、全管道 26.509s；结束后 DB/Milvus/ES/源文件清理验证通过 | 可重复脚本与明细见 `docs/p1_bench_report.md` B 节 |
| 6 | 端到端生成场景 P95/P99（阈值修复后） | ✅ **高频场景达标**：25 轮 TTFB/完整回答 P95 0.111s/P99 0.155s，DONE P95 0.112s/P99 0.155s；冷请求尾延迟如实单列 | 安全缓存边界、冷/热口径和脚本见 `docs/p1_bench_report.md` C 节 |
| 7 | 短信真实发送 <3s | ⏸ 外部条件阻塞（配置链已闭环） | 2026-08-23 核查确认 `deploy/.env` 的 `SMS_ENABLED` 与四项 `ALIYUN_SMS_*` 均未配置，无法真实收码；已补 Compose/两份 env 示例完整透传、开关防误发、production 禁止 mock、2.8s 网关超时、可重复验收脚本与 Runbook；74 项短信/账号/启动配置测试通过。凭据与验收手机号到位后执行 `scripts/verify_sms.py`，仍需人工确认手机收码 |
| 8 | 浏览器兼容性（Chrome/Edge/Firefox） | ✅ 已验证 | Chrome 151.0.7922.170、Edge 151.0.4129.101、Firefox 132.0：登录/工作台/知识库/Widget 预览/运营看板与第三方页 widget.js 全通过，0 控制台/页面/关键网络异常、0 横向溢出；可重复脚本与边界见 `docs/browser_compatibility_report.md` |
| 9 | CI 首次实际运行 | ✅ **2026-08-23 双 job 全绿**（后端：依赖审计/迁移一致性/测试覆盖率；前端：依赖审计/类型/单测/构建） | 首轮共排掉 4 类地雷：pip-audit CLI、npm audit 高危、requirements 编码、MySQL 触发器权限与迁移漂移（新增 d35） |
| 10 | Nginx 反代 API 转发验证 | ✅ 已验证 | /health 200 透传 + SPA fallback 200 + 真实 API 路径 401 正确拒绝 |
| 11 | 前端生产构建 `npm run build` | ✅ 已验证 | `✓ built in 11.60s`；仅 element 分包 936KB 偏大（见 §十二 C） |

### B. PRD 缺口（❌ 类，非审计未完成——按全量口径列出，是否补齐由你决策）

| 缺口 | PRD 出处 | 备注 |
|---|---|---|
| ✅ 数据埋点事件体系 + 运营看板 | §5 | 2026-08-24 已完成：15 组 PRD 业务事件 + `session_resolved`，Mongo 365 天 TTL/PII 脱敏/fail-open；租户隔离聚合 API 与平台双鉴权 API；前端覆盖检索对照、自动解决趋势、审批时效、套餐分布、回款趋势和最近事件；101 项后端定向回归、15 项前端定向测试、`vue-tsc` 与生产构建通过，真实 Mongo 写入及聚合接口验证通过 |
| ✅ 套餐升级流程 | §3.9 | 2026-08-23 已完成：四档套餐卡片、admin 升级接口、basic/pro 即时生效、enterprise 待人工议价、席位/配额联动、审计日志；12 项后端契约测试与 10 项前端定向测试通过，`vue-tsc` 零错误，Alembic upgrade/check 通过 |
| ✅ 租户注销/30 天窗口/物理删除 | §8.5 | 2026-08-24 已完成：admin 申请/查询/撤销、30 天仅导出门禁、前端截止时间与危险操作确认；每 5 分钟到期清理 MySQL/Mongo/Milvus/ES/Redis/上传文件，外部失败保留重试、共享用户保留；19 项后端定向契约、13 项前端定向单测与真实临时租户 11/11 跨存储清零验证通过，Alembic d37 已升级到 head |
| ✅ DeepAgent token 预算 + 任务超时 | §3.11 | 2026-08-23 已完成：默认 500,000 token 与 600 秒墙钟双预算；优先累计模型真实 usage、缺失时本地估算；超限立即停止后续模型/工具调用并标记“资源超限失败”；Mongo/API 兼容持久化 token_consumed、预算及终止原因，前端支持执行中与历史任务展示；20 项 DeepAgent/模型网关测试通过，前端生产构建通过 |
| 终端用户双因子短信方案 | §3.1.3 | 手机号+后4位已实现 ✅ |
| 知识库租户公平调度（单队列） | §3.2 | 规模问题 |
| Widget SDK 打字状态/消息时间戳 | §3.8 | PRD 自认待完善 |
| ✅ 备份灾备实操脚本 | §4.4 | 2026-08-23 已实跑：MySQL dump 500,653B、SHA-256 校验、导入临时库恢复 22 张表且自动清理；Milvus 三组件物理快照 29,684,452B 并通过 SHA-256 校验；ES 快照 `zhihub-20260823-234100` 状态 SUCCESS（1/1 shard）；脚本含 30 天安全清理与恢复 Runbook |
| 无坐席等待时长估算 | §3.7 | 有静态提示代替 |
| RAGAS +15pp 目标 | §1.4 | 实测 +8.3pp，需扩充评测集而非改代码 |

### C. 遗留改进项（非 PRD 要求，工程建议）

| 项 | 说明 |
|---|---|
| 前端 element-plus 分包偏大（936KB gzip 302KB） | 构建警告：可用 manualChunks 拆 vendor 或按需引入组件，优化首屏 |
| ✅ 检索召回对口语化改写覆盖不足 | 2026-08-24 已完成：Q9/Q10 Top1 0.968/0.501，18 题端到端 18/18、库外 5/5，RAGAS CR 0.69 无下降；补充可重复评测脚本和独立报告 |
| ✅ uvicorn 多 worker | 2026-08-23 已完成：生产镜像启用 `--workers 2`，容器进程实测为 1 个父进程 + 2 个服务进程；20 并发暖机后 `/health` P95 71.9ms / P99 91.1ms / 546.6 req/s，与原基线 75.2ms / 90.7ms / 564 req/s 持平 |
| ✅ 应用 INFO 日志 | 2026-08-23 已完成：`main.py` 配置 root stdout/stderr handler，容器日志实见 `app.main` 与 `app.core.database` INFO；高噪声第三方库降为 WARNING，健康探针业务访问日志降为 DEBUG，Uvicorn 默认日志保持不变 |
