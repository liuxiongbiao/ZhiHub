# ZhiHub 后端盘点报告（代码级，只读审查）

> 盘点日期：2026-08-17 | 范围：backend/app + backend/scripts + backend/tests + deploy | 本轮未修改任何代码

## 1. API v1 Router 清单（`backend/app/api/v1/`，挂载见 `backend/app/main.py` L239-251，统一前缀 `/api/v1/<module>`）

### auth.py（`/api/v1/auth`）
| Method + Path | 功能 | 权限 |
|---|---|---|
| POST `/register` | 手机号注册（建用户+租户+管理员） | 公开 |
| POST `/login` | 手机号+密码登录，签发 access/refresh | 公开 |
| POST `/refresh` | 刷新 token（轮换） | 公开 |
| POST `/logout` | 登出（jti 黑名单） | 登录 |
| POST `/tokens/revoke-user` | 撤销用户全部 refresh token | `require_role("admin")` |
| POST `/ws-token` | 签发 1 分钟工作台 WS 短效令牌（`ws: True` claim） | agent/admin |
| POST `/sms/send`、`/code/send` | 发验证码 | 公开（限流） |
| POST `/login/sms` | 验证码登录 | 公开 |
| POST `/register/email`、`/login/email`、`/email/activate`、`/email/resend` | 邮箱注册/登录/激活 | 公开 |
| POST `/password/reset` | 重置密码 | 公开 |
| POST `/bind/email`、`/bind/phone` | 绑定邮箱/手机 | `get_current_user` |
| GET `/wechat/auth`、`/wechat/callback`、POST `/wechat/bind` | 微信扫码登录/绑定 | 公开 / 登录 |

### session.py（`/api/v1/session`）- 会话生命周期
| Method + Path | 功能 | 权限 |
|---|---|---|
| POST `` | 创建会话 | 登录（tenant_id 必须存在） |
| GET `/list` | 会话列表（分页+筛选） | 登录 |
| GET `/quick-replies`、POST `/quick-replies` | 坐席快捷回复模板查/建 | 登录 |
| GET `/{session_id}` | 会话详情（含消息） | 登录 |
| POST `/message` | 用户发消息驱动 Agent | 登录 |
| POST `/message/stream` | SSE 流式输出 | 登录 |
| POST `/{session_id}/agent-message` | **认领坐席**人工回复 | `require_role("agent","admin")` |
| POST `/{session_id}/claim` | 坐席认领（Redis SETNX 锁） | 登录（无 role decorator，靠业务校验） |
| POST `/{session_id}/return-to-agent` | 坐席退回 AI | 登录 |
| GET `/{session_id}/trace` | MongoDB Agent 执行轨迹 | 登录 |
| POST `/{session_id}/leave-message` | 无在线坐席留言建单（category=message） | 登录 |
| POST `/{session_id}/csat` | 会话满意度评价（closed 后，一次） | 登录 |
| POST `/message/{message_id}/feedback` | 单条消息 👍/👎 反馈 | 登录 |

### knowledge.py（`/api/v1/knowledge`）
| Method + Path | 功能 | 权限 |
|---|---|---|
| POST `/upload` | 上传文档（expires_days 0-3650） | `require_role("admin")` |
| GET `/list` | 文档列表 | `require_role("admin")` |
| POST `/{doc_id}/rebuild` | 重建索引（202） | `require_role("admin")` |
| DELETE `/{doc_id}` | 删除文档（含检索切片） | `require_role("admin")` |
| POST `/test` | 检索测试台：纯向量 vs 混合检索对比 | `require_role("admin")` |

### ticket.py（`/api/v1/ticket`）- 全部登录即可（无 role decorator）
- POST `` 创建工单（category 默认 general）、GET `/list` 工单列表、GET `/{ticket_id}` 工单详情。

### order.py（`/api/v1/order`）- 退款（refund）端点就在这个 router 里
| Method + Path | 功能 | 权限 |
|---|---|---|
| POST `` | 创建订单 | `require_role("admin","agent")` |
| GET `/list` | 订单列表 | 登录 |
| GET `/{order_id}` | 订单详情 | 登录 |
| GET `/refunds/list` | 退款审批列表 | `require_role("admin","agent")` |
| GET `/refunds/rules` | 退款规则 | `require_role("admin","agent")` |
| POST `/refunds/request` | 发起退款申请 | `require_role("admin","agent")` |
| POST `/refunds/{approval_id}/approve` | 审批退款 | `require_role("admin","agent")` |
| POST `/refunds/{approval_id}/cancel` | 撤销退款 | **`require_role("admin")`** |
| POST `/{order_id}/ship` | 发货（写 tracking_no） | `require_role("admin","agent")` |

### billing.py（`/api/v1/billing`）- 用量/统计端点所在
| Method + Path | 功能 | 权限 |
|---|---|---|
| GET `/quota` | Redis 实时配额（count/quota/remaining） | `require_role("admin")` |
| GET `/invoices` | 月度账单列表 | `require_role("admin")` |
| GET `/usage` | 用量概览（仅复用 check_quota） | `require_role("admin")` |
| GET `/usage/export` | 用量 CSV（usage_records 日聚合） | `require_role("admin")` |
| GET `/usage/export-detail` | 会话级 Token 明细 CSV | `require_role("admin")` |
| POST `/pay/{invoice_id}` | 支付宝下单+支付链接 | `require_role("admin")` |
| POST `/alipay/notify` | 支付宝异步回调 | 公开（auth 白名单，验签保护） |

### tenant.py（`/api/v1/tenant`）
- GET `/home`（登录+`require_tenant`）；GET `/billing`（admin）；GET/POST `/wizard`、`/wizard/next`（admin）；POST `/invite`、`/invite/accept`（accept 公开）；GET `/audit-logs`（admin）；POST `/enterprise/approve`（**X-Platform-Token 独立鉴权**，非 admin JWT 权限，`tenant.py` L278-279 比对 `_settings.platform_ops_token`）；POST/GET `/widget`、`/widget/embed-code`、`/widget/api-key`、PUT `/widget/domains`（admin）；GET/PUT `/refund-threshold`（admin）；GET `/export`（admin，数据导出）。除 `/home`、`/invite/accept`、`/enterprise/approve`（平台令牌）外全部 `require_role("admin") + require_tenant()`。

### widget.py（`/api/v1/widget`）
- POST `/session`（公开，API Key + 域名白名单）；POST `/{session_id}/message`（Widget 令牌）；POST `/refunds/{approval_id}/cancel`（Widget 令牌 + 手机号后 4 位）。

### ws.py（`/api/v1/ws`）
- WEBSOCKET `/agent`（坐席工作台）、WEBSOCKET `/session/{session_id}`（终端用户）。

### agent.py（`/api/v1/agent`）
- POST `/deep-task`（DeepAgent 深度任务，Celery 异步/同步 fallback）`require_role("admin","agent")`；GET `/deep-task/{task_id}`（查任务状态/报告，登录，校验 tenant 归属）。

### mcp/server.py（`/api/v1/mcp`）
- GET `/tools/list`（返回 TICKET_TOOLS + ORDER_TOOLS）；POST `/tools/call`（JSON-RPC 2.0 信封 -> mcp_service 分发；role/tenant_id 从 JWT 注入不信任客户端；内部走 `ROLE_TOOL_MATRIX` 权限矩阵：admin 可 request_refund，agent 可查不可退，user/unknown 全拒）。

### notification.py（`/api/v1/notifications`）
- GET ``（站内信列表）、GET `/unread-count`、POST `/{notification_id}/read`。登录即可。

### stats/analytics/dashboard
- **没有独立的 stats/analytics/dashboard router 或端点**。全部统计能力收敛在 `billing.py` 的 `/usage*` 端点 + `/metrics`（Prometheus，`main.py` L262，含 ACTIVE_SESSIONS gauge）。

## 2. widget.py 与 ws.py 鉴权及事件

**widget.py**（`backend/app/api/v1/widget.py`）
- `POST /session` 公开（auth_middleware 白名单，`auth_middleware.py` L60）：body 携带租户 `api_key`（库里只存 SHA-256 哈希，`tenant.widget_api_key_hash`）+ origin -> `widget_service.create_widget_session` 校验 key + 域名白名单 -> 返回 `{session_id, token, welcome_text}`。token 是 JWT，claims 为 `role="widget"`, `tenant_id`, `session_id`（`widget_service.py` L91-96）。
- 后续请求：`auth_middleware.py` L105-113 强制 `role=widget` 的 token 只能访问 `/api/v1/widget/*`，注入 `request.state.widget_session_id`，`user_id` 保持 None（匿名）。发消息端点校验路径 session_id == token session_id（403）、租户级+会话级双重限流（429）。

**ws.py**（`backend/app/api/v1/ws.py`）
- `/agent` 坐席工作台：JWT 走 **query 参数 `?token=`**（浏览器 WS 不支持自定义头）；校验签名/过期 -> jti 黑名单（Redis 故障 fail-open）-> role ∈ {agent, admin} -> 必须是 `/ws-token` 签发的 `ws: True` 短效令牌（1 分钟）-> `manager.connect` 按租户分桶，跨实例走 Redis Pub/Sub（`ws_manager.py`）。
- `/session/{session_id}` 终端用户通道：同样 query token JWT；`role=widget` 时要求 token 内 session_id 与路径一致；并查库校验会话归属租户。只收推送，不处理上行（receive_text 空循环保活）。
- **事件**（envelope `{"type", "payload"}`，见 ws_manager 与各广播点）：
  - 服务端->坐席：`new_session`（转人工 waiting_human，`agent_service.py` L85）、`session_claimed`（`human_service.py` L108）、`typing`（坐席输入状态，`ws.py` L144）。
  - 服务端->终端用户：`agent_message`（人工坐席回复，`human_service.py` L146）。
  - 客户端->服务端（坐席）：`{"type":"typing","session_id","is_typing"}`、`{"type":"heartbeat"}`（刷新在线 presence），非 JSON 视为心跳忽略。

## 3. ORM 模型清单（`backend/app/models/`）

| 文件 | 表名 | 说明 |
|---|---|---|
| user.py | `users` | 平台账号；phone/email/wechat_openid AES-GCM 加密 + *_hash HMAC 等值查唯一；role_default |
| tenant.py | `tenants` | 租户：plan_type、quota_conversations_per_month、seat_limit、status(active/readonly/closed)、wizard_step、refund_approval_threshold、widget 品牌字段、widget_api_key_hash/domains、default_model |
| tenant_member.py | `tenant_members` | 租户-用户多对多，role(admin/agent)，(tenant,user) 唯一 |
| session.py | `sessions` | 会话状态机（见下） |
| message.py | `messages` | 消息流水（见下） |
| ticket.py | `tickets` | 工单：ticket_no、status(open/in_progress/resolved/closed)、priority、assignee_id、**phone（加密，双因子）**、**category（general/message/扩展）**、created_by(agent/staff/message) |
| order.py | `orders` | 订单：order_no、amount/paid_amount(Numeric)、status 状态机(created->paid->shipped->completed；->refunding->refunded)、version 乐观锁、**phone（加密）**、payment_trade_no、tracking_no、created_by |
| refund_approval.py | `refund_approvals` | 退款审批当前态投影：order_id、amount、status(pending/approved/rejected)、client_request_id 幂等键、approver_id、review_history |
| refund_approval_event.py | `refund_approval_events` | 不可变审批事件流水（ORM 事件监听禁 UPDATE/DELETE） |
| refund_outbox.py | `refund_outbox` | 退款执行外发队列（租约重试 + last_error） |
| knowledge_document.py | `knowledge_documents` | 文档元数据：status(pending/processing/ready/failed)、version/is_latest、chunk_count、expires_at、quality_status(ready/pending_review/expired) |
| usage_record.py | `usage_records` | 按租户+日聚合用量：date、conversation_count、snapshot_total、is_overage（无 token/命中维度） |
| billing_event.py | `billing_events` | 会话计费事件：session_id 唯一、event_key、model、**tokens**、usage_amount(Numeric 16,6)、status/attempts（消费投影） |
| billing_invoice.py | `billing_invoices` | 月度账单：period、base_fee、overage_fee、status(unpaid/paid)、payment_trade_no、paid_at |
| answer_feedback.py | `answer_feedback` | 单条消息 👍/👎：score(1/-1)、bad_case、comment |
| csat.py | `csat_evaluations` | 会话级满意度：feedback(like/dislike)、score 1-5、comment |
| quick_reply.py | `quick_replies` | 坐席快捷回复模板 |
| notification.py | `notifications` | 站内信（quota_warning/handoff/refund/system） |
| audit_log.py | `audit_logs` | 审计日志（退款/登录/向导/支付） |
| refresh_token.py | `refresh_tokens` | 可轮换 refresh token（family/parent/revoked） |
| security_incident.py | `security_incidents` | Prompt 注入拦截记录 |

**session/message 关键字段**：
- `sessions`：status（`created/routing/agent_handling/resolved/closed` + `waiting_human ↔ human_handling` 人工回路）、intent、confidence、sentiment、assignee_id（handoff 由 status=waiting_human/human_handling + assignee_id 表达，无独立 handoff 列）、end_user_id、effective_reply、billed。**会话表没有 category 列**--category 在 `tickets` 上。
- `messages`：session_id、role(user/assistant/agent/system)、content、sources(JSON 引用)、msg_metadata(JSON)。**msg_metadata 已保存**：tokens、elapsed_ms、intent、confidence、sentiment、degraded、quota_status（写入点见 `agent_service.py` L272/L607/L655）。**没有显式的检索命中/未命中标记字段**，但 `sources` 为空/缺失可作初步知识缺口信号。

**knowledge_miss / gap / faq 专用表：不存在**（详见第 6 节）。feedback 有两张表：`answer_feedback`（消息级）+ `csat_evaluations`（会话级）；无 faq/knowledge_gap 表。

## 4. 权限体系

- **角色**：无正式 Enum 类，是字符串约定。JWT `role` claim 的取值：`admin`（租户管理员）、`agent`（坐席）、`user`（MCP 矩阵里出现）、`widget`（匿名 Widget 终端用户）。租户内角色存 `tenant_members.role`（admin/agent，`models/tenant_member.py`）；全局默认 `users.role_default`。MCP 工具白名单矩阵 `ROLE_TOOL_MATRIX` 在 `services/mcp_service.py` L52；WS 角色白名单 `_ALLOWED_ROLES = {"agent","admin"}` 在 `api/v1/ws.py` L45。
- **deps**（`backend/app/api/deps.py`）：`get_current_user`（request.state.user_id 查库，无 -> 401）、`require_role(*roles)` 依赖工厂（role 不在白名单 -> 403）、`require_tenant()`（加载 Tenant，closed -> 403）。中间件负责"是谁"（`auth_middleware.py` 注入 user_id/role/tenant_id/widget_session_id；`tenant_middleware.py` 搬运 tenant_id），deps 负责"能不能做"。

## 5. services 层（`backend/app/services/`）

| service | 职责 |
|---|---|
| agent_service.py | 用户消息入口：驱动 LangGraph、trace 写 MongoDB、配额计数、LLM 兜底、token 预算截断、轮次上限；转人工时广播 `new_session` |
| auth_service.py | 注册事务（用户+租户+关联）、登录校验（锁定）、登出黑名单 |
| billing_service.py | 配额 Redis 计数 + 80%/100% 预警、月账单生成、mock 支付宝支付/回调、用量 CSV 导出（含会话级 token 明细）、逾期降级 |
| human_service.py | 转人工条件判断、Redis SETNX 认领互斥、退回 Agent、认领坐席回复推送、无坐席留言建单 |
| idempotency_service.py | 业务级幂等（client_request_id 缓存 + SETNX 抢占） |
| knowledge_service.py | 文档上传校验、状态流转、版本归档、重建任务发布、存储上限 |
| mcp_service.py | MCP 守门人：权限矩阵、熔断、入参校验、双因子（phone 后 4 位）、JSON-RPC 封装 |
| memory_service.py | 长期记忆：LLM 抽取事实 -> 向量化 -> Milvus 存取 |
| notification_service.py | 站内信创建/列表/未读/已读 |
| order_service.py | 资金安全核心：状态机 + 乐观锁 + 退款上限 + 大额分级审批 + outbox 补投 |
| rate_limit_service.py | 租户/用户/会话多维 ZSET 滑窗限流 |
| retrieval_service.py | 混合检索：查询改写 -> Milvus 向量 + ES BM25 双路 -> RRF 融合 -> bge-reranker 精排 -> 低置信度 HyDE 兜底 -> 降级标记（`low_confidence` 会触发强制转人工） |
| security_service.py | Prompt 注入规则检测 + security_incidents 落库 |
| session_service.py | 会话生命周期、Redis 上下文、轮次计数转人工、坐席 presence、CSAT |
| tenant_service.py | 配置向导状态机、成员邀请 |
| ticket_service.py | 工单校验 + 幂等 + 状态机 |
| trace_service.py | Agent 轨迹写/查 MongoDB |
| widget_service.py | Widget API Key 哈希校验、域名白名单、匿名会话令牌签发 |

**没有独立的 analytics/统计 service**--统计散在 billing_service 与 metrics（`core/metrics.py` Prometheus）。

## 6. 知识未命中记录 / FAQ 候选生成：专用模型未实现（信号已有）

全局 grep `miss/gap/faq/candidate/no_result/未命中/无结果` 结论：
- **无任何 knowledge_miss / knowledge_gap / faq / candidate 落库表或模型**；知识缺口的审核状态与 FAQ 候选工作流完全没有实现，需要新增持久化模型。
- 唯一"未命中"处理是运行时兜底，不落库：`agents/nodes/knowledge_agent.py` L47（无结果返回"（无检索结果）"）、L130+（低置信度 -> `should_interrupt=True` 强制转人工，回复固定话术；检索异常 -> 置空走"建议联系人工客服"）。
- **已有可用的初步信号**（无需迁移即可做近似分析）：
  - `messages.msg_metadata` 已保存 intent、confidence、sentiment、degraded、quota_status；
  - `messages.sources` 为空/缺失 ≈ 未命中；
  - `answer_feedback.bad_case`（👎 标记）。
- `retrieval_service.py` L490/517 的 `candidates` 只是 rerank 输入变量，与 FAQ 候选无关。
- `scripts/seed_knowledge.py` 里的 "FAQ" 仅为种子文档文件名。
- 但缺口的精确采集（query 原文、置信度阈值、是否转人工的专用记录）与审核状态机均无持久化实现。

## 7. 现有统计类能力

- **usage_records 维度**：仅 `tenant_id + date + conversation_count + snapshot_total + is_overage`--**没有** token、检索命中率、解决率维度。
- **token 维度**只存在于两处非聚合位置：`billing_events`（model/tokens/usage_amount）和 `messages.msg_metadata.tokens`（JSON，由 export 时现算累加）。
- **billing 统计端点返回**：`GET /usage` -> `{"quota": {count, quota, remaining}}`（Redis 实时计数）；`GET /usage/export` -> 日聚合 CSV（tenant_id,date,conversation_count,is_overage）；`GET /usage/export-detail` -> 会话级 CSV（session_id, occurred_at, intent_type, model, tokens, is_overage，token 折算 `tokens/2500` 累计对比套餐额度，`billing_service.py` L605）。
- **无会话解决率 / 转人工率 / 检索命中率 / CSAT 聚合端点**--CSAT 和 answer_feedback 只有写入（POST），没有任何查询/统计 API。Prometheus `/metrics` 只有 HTTP 层指标 + ACTIVE_SESSIONS gauge。

## 8. 脚本清单（backend/scripts + backend/e2e-seed.py）

| 脚本 | 职责 |
|---|---|
| scripts/seed_data.py | 业务种子数据（工单/订单/退款审批） |
| scripts/seed_knowledge.py | 知识库语料灌入（Milvus + ES + DB） |
| e2e-seed.py（backend/ 根） | E2E 演示种子：waiting_human/closed 会话、账单、快捷回复、工单、订单、退款审批 |
| scripts/verify_e2e.py | 端到端验证，逐子系统输出 PASS/FAIL 清单 |
| scripts/ragas_eval.py | RAGAS 三指标评测（纯向量 vs 混合检索，DeepSeek 作 judge） |
| scripts/agent_eval.py | Agent 评测（10 条测试集：意图识别 + 工具调用准确率） |
| scripts/load_test.py | HTTP 压测（P50/P95/P99、吞吐） |
| scripts/real_mysql_smoke.py | 真实 MySQL HTTP 全链路 smoke（fakeredis + 真实 DB） |
| scripts/smoke_r2_fixes.py | 第二轮修复真实环境冒烟（真实库 + 真实 Redis） |
| scripts/scan_connectivity.py | 逐文件 import 连通性扫描 |

### seed_data.py 具体造了什么
- **不创建租户、不创建账号**（租户硬编码 `tenant_id=1`；账号靠前端注册流程或 e2e 脚本现场注册）。
- **5 条工单**（TK-20240001~00005，登录缓慢/套餐升级/改地址/发票/异常登录，priority high~low），全部绑定手机号 `13800000001`。
- **5 条订单**（ORD-20240001~00005，89~1299 元，状态 paid/shipped/completed/created），同样绑定 `13800000001`。
- **1 条 RefundApproval**：ORD-20240001 置为 `refunding` 并挂 100 元退款审批。
- 幂等设计：已存在则跳过或只补 phone 列，可重复执行。
- **无会话（session/chat）种子数据。**

### e2e-seed.py（`backend/e2e-seed.py`，注意位置在 backend/ 根而非 scripts/）
- 为 E2E/浏览器测试准备的演示种子，**幂等**（已存在则跳过或重置），tenant 硬编码 `TENANT_ID`。创建：
  - **waiting_human 会话**（intent=order_query、confidence=0.31、sentiment=unhappy，供坐席工作台认领/退回/Trace 测试；重复执行会重置为 waiting_human 并释放认领）；
  - **closed 会话**（intent=knowledge_consult，供 CSAT 评价测试）；
  - **月度账单**（BillingInvoice，多期）；
  - **坐席快捷回复**（QuickReply）；
  - **工单 / 订单 / 退款审批**（与 seed_data.py 类似语义）。
- **仍不创建租户和账号**（依赖已存在的租户与登录流程），也**不是完整电商演示租户**（无商品/店铺语义与客户账号）。

### seed_knowledge.py
- 给 tenant 1 灌 21 份中文 Markdown 知识文档（产品手册/退款政策/FAQ/订单目录/工单目录/套餐/4 个 SOP/支付/物流/发票/SLA 等），先清空 Milvus + ES + DB 旧数据再切片、embedding、双写索引。
- **没有"电商演示租户"**--订单/商品数据全是"智枢 SaaS 套餐"语义，无商品/店铺类独立租户。

## 9. backend/tests 结构

`pytest --collect-only` 实测：**689 个测试**（VALIDATION_REPORT 记录 687 passed, 2 skipped）。

```
backend/tests/
├── conftest.py          # 全局夹具
├── contracts/ (44 个文件)  # 契约/路由级测试：计费、退款、MCP、PII、WebSocket、微信登录、OCR、观测性等
├── integration/ (10 个)    # test_alembic_from_scratch / test_auth_middleware / test_knowledge_api
│                           # / test_mcp_security_e2e / test_009_010_e2e / test_session_lifecycle_e2e 等
├── e2e/ (1 个)             # test_account_flow.py（注册->登录->登出->越权 403 全链路，8 个 test）
└── unit/ (13 个)           # file_parser / retrieval / order_state_machine / mcp_permission / security 等
```

### conftest.py mock 策略
- **MySQL -> SQLite 内存库**（StaticPool 共享连接，每测试建表/销表）；`client` 夹具覆盖 `get_db`，auth/tenant 中间件链路保持真实。
- **Redis -> fakeredis**（autouse，替换 `core.redis._redis_client` 单例）。
- **支付宝/微信/OCR -> 强制 mock 模式**（autouse 清空 `alipay_app_id`/`wechat_app_id`/`ocr_*` 并 reset 客户端单例）。
- **Langfuse -> 清空 key**（禁止测试向真实账号导 trace）。
- LLM/Milvus/ES 不在全局 conftest mock，由各测试自行处理（`APP_ENV=test` 下 LLM/EMBEDDING 默认关）。
- 运行方式：`cd backend && .venv/Scripts/python.exe -m pytest`（pyproject `[tool.pytest.ini_options] testpaths=["tests"], asyncio_mode="auto"`）。

## 10. deploy 目录

### docker-compose.yml 服务清单
- **中间件**：mysql(8.0, 端口 3307)、mongo(7.0)、redis(7)、rabbitmq(3.12)、elasticsearch(8.13)、etcd、minio、milvus(v2.4.0)
- **应用**：`migration`（一次性 alembic upgrade head，backend 启动前置）、`backend`(:8000)、`celery-worker`(maintenance/celery 队列)、`celery-knowledge-worker`(knowledge 队列)、`celery-beat`、`frontend`(:5173->80)
- **入口**：`nginx`(alpine, :80)
- **observability profile（默认关）**：langfuse-db(postgres)、clickhouse、langfuse(v3, :3000)

### nginx.conf 反代路径
| location | 目标 |
|---|---|
| `/api/v1/ws/` | backend:8000，WebSocket upgrade，read_timeout 3600s（最长前缀，先于 /api/ 匹配） |
| `/api/` | backend:8000（带 upgrade 头，read_timeout 120s） |
| `/health` | backend:8000 |
| `/` | frontend 容器（SPA，Vue Router history fallback） |

## 11. 测试相关依赖

**backend/requirements-dev.txt**：pytest==9.1.1, pytest-asyncio==1.4.0, fakeredis==2.37.0, pytest-cov==7.1.0, pip-audit>=2.7

## 12. 关键结论

1. 后端匿名 Widget 链路（API Key + 域名白名单 + widget JWT + 匿名消息 API + 终端用户 WS 下行通道）已完整；前端 SDK `frontend/public/widget.js`（经 `/widget.js` 提供）已能匿名建会话与发消息，但**未连接 `/api/v1/ws/session/{session_id}`**（收不到 agent_message），也无人工接入状态/引用/CSAT/工单订单退款展示；embed code 用相对地址，外站粘贴不能直接工作。
2. 统计能力收敛在 billing，无独立 stats 服务/端点；CSAT 与 answer_feedback 只有写入无查询。
3. 知识缺口闭环（未命中精确落库 -> 缺口列表 -> FAQ 候选审核状态机）零实现，是唯一需要新持久化模型+新业务流的块；`messages.msg_metadata`（intent/confidence/sentiment/degraded/quota_status）与 `sources` 可作初步信号。
4. seed 数据薄：seed_data.py / e2e-seed.py 均不创建租户和账号，无电商演示租户；e2e-seed.py 已能造 waiting_human/closed 会话、账单、快捷回复、工单、订单、退款审批。
5. 后端 689 测试全离线（SQLite + fakeredis + 支付/OCR mock），真实 MySQL/Redis 验证靠 smoke 脚本而非 pytest。

## 13. 模块判定总表（已有 / 部分已有 / 缺失）

| 模块 | 判定 | 依据 |
|---|---|---|
| 角色入口 | **部分已有** | admin/agent 登录+JWT+守卫完整；最终客户有 widget 匿名通道（后端完整 + widget.js SDK 可用），但无公开 Vue 聊天页 |
| 管理员看板 | **缺失** | 无 stats 聚合端点与看板页；仅有 billing usage（配额/账单/CSV） |
| 客户 Widget | **部分已有** | 已有：API Key（哈希存储）、域名白名单、Widget JWT、匿名会话/消息 API、终端用户 WS 后端（`agent_message` 下行）、`/widget.js` SDK；缺失：SDK 未连 WS、无人工接入状态/引用/CSAT/工单订单退款展示、embed code 相对地址外站不可直接用 |
| 客服工作台 | **部分已有** | 会话队列/认领/退回/人工回复/Trace/WS 实时/快捷回复全有；缺工作台内的客户上下文面板（订单查询/建单/退款发起） |
| 演示租户 | **缺失** | seed 不建租户与账号，订单/知识全是 SaaS 套餐语义，无电商租户与客户账号 |
| 知识缺口采集 | **部分已有** | 无专用落库表；但 `messages.msg_metadata`（intent/confidence/sentiment/degraded/quota_status）与 `sources` 已保存，可作初步信号 |
| 知识缺口处理 | **缺失** | 无缺口聚合/列表/分析，无审核状态持久化 |
| FAQ 候选审核 | **缺失** | 无候选生成、无审核工作流，需新增持久化模型（候选表 + 状态机） |
