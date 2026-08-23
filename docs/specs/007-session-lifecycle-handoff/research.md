# Research: 会话生命周期与转人工增强

> Phase 0 产出：解决 Technical Context 中的未知项与技术选型。澄清决策见 spec.md `## Clarifications`。

## R1: 轮次计数存储与键设计

**Decision**: Redis，会话级键 `session:rounds:{session_id}` 存 JSON `{total, consecutive, last_intent}`，TTL 30 分钟（与空闲超时同窗，会话活跃时滚动续期）

**Rationale**:
- 轮次计数高频读写（每轮消息 incr），Redis 性能远优于 MySQL
- 会话级 TTL 自动清理：会话空闲超时后计数自然失效，无需手动清
- 多 worker 共享（限流/转人工判断需一致视图）
- `consecutive` 记录连续未解决轮次：当本轮 intent == last_intent 且为可解决类意图（ticket_query/order_query/sensitive_action）时 consecutive++，否则 consecutive=0；知识咨询不计入
- `total` 累计总轮次，达 15 转 artificial

**Alternatives**:
- MySQL：高频读写不适合，且轮次是瞬态。否决
- 进程内存：多 worker 不共享。否决

## R2: 空闲超时定时任务机制

**Decision**: 应用内后台线程（APScheduler 简化版 / while-sleep 循环），每 5 分钟扫一次超时会话

**Rationale**:
- demo 单体部署，不引入 celery beat 基础设施（与现有 knowledge consumer 后台线程同模式）
- 扫描逻辑：查 status in (AGENT_HANDLING, WAITING_HUMAN) 且 updated_at < now-30min 的会话 -> 转 RESOLVED；查 status=RESOLVED 且 updated_at < now-60min 的 -> 转 CLOSED（RESOLVED->CLOSED 再过 30min，即总 60min）
- 竞态处理：更新前重新校验 updated_at（避免与用户并发消息冲突），用乐观条件 `WHERE id=? AND updated_at<?`
- 容错：单次扫描异常不致命，下次扫描恢复

**状态流转**:
- AGENT_HANDLING / WAITING_HUMAN（30min 无活动）-> RESOLVED
- RESOLVED（再 30min 无活动）-> CLOSED

**Alternatives**:
- celery beat：需额外 worker + 调度配置，demo 过重。否决
- Redis 键过期回调（keyspace notifications）：不可靠（通知可能丢），且需额外配置。否决

## R3: 在线坐席 presence 实现

**Decision**: Redis Set `agents:online:{tenant_id}`，login 时 SADD user_id（仅 role=agent），logout 时 SREM；"无在线坐席" = SCARD == 0

**Rationale**:
- 标准 presence 模式，判定 O(1)（SCARD）
- login() 已有 role/tenant_id/user_id，SADD 一行；logout 端点从 request.state 取 user_id/tenant_id/role 传给 logout() SREM
- 复用现有 Redis 基建

**已知限制**（demo 可接受）:
- 坐席 token 过期但未显式 logout -> 在线集合残留 stale 条目（留言建单可能不触发）。生产应用 heartbeat + per-agent TTL key 自动过期；demo 阶段坐席显式 logout，且 5min 认领锁 TTL 已覆盖大部分场景
- 不跟踪坐席容量（一个在线坐席即视为有坐席），demo 够用

**Alternatives**:
- 查 human_handling 会话：把"在岗"等同"正处理会话"，刚登录未认领坐席被误判离线。否决
- per-agent TTL key + heartbeat：最健壮但需心跳端点 + 中间件刷新，demo 过重。列为未来增强

## R4: CSAT 满意度接入

**Decision**: 复用现有 CsatEvaluation 模型（session_id/message_id/feedback/score/comment）+ 新建 crud/csat.py + POST /api/v1/session/{id}/csat 端点

**Rationale**:
- 模型已存在（Day 6 建），本 feature 只接入端点 + 校验
- csat_evaluations 无 tenant_id 列：通过 session 归属校验 tenant（端点从 JWT 取 tenant_id，校验 session.tenant_id 匹配后再创建 CSAT），满足宪法 II 间接隔离
- 仅 CLOSED 会话可评价；同一 session 仅一次（查询 csat_evaluations by session_id 去重）
- 请求体：`{score: 1-5, comment?: str}`；feedback 字段（like/dislike）本 feature 不用（兼容现有 👍/👎，CSAT 用 score）

**Alternatives**:
- 给 csat_evaluations 加 tenant_id 列：冗余（session 已有 tenant_id），增加迁移。否决，间接校验足够

## R5: 留言建单 category 字段

**Decision**: Ticket 加 `category` 列（VARCHAR(30), nullable, default "general"）；留言建单设 `category="message"`

**Rationale**:
- 可查询（列出留言工单）、支持未来分类（投诉/退款）
- 与 006 加 phone 列同模式（alembic 迁移 d6_ticket_category，down_revision=d5_ticket_phone）
- nullable + default 保证向后兼容现有工单数据

**Alternatives**:
- 标题前缀【留言】：不可查询、不规范。否决
- 复用 priority：语义错。否决

## R6: 终端用户级对话限流接入

**Decision**: 复用 rate_limit_service，新增 `check_message_rate_limit(user_id)` 10次/分钟（独立键 `rate:msg:{user_id}:minute`，滑动窗口 ZSET），在 /session/message 端点层校验，超限返回 HTTP 429 + 友好 detail，不落库不计轮次

**Rationale**:
- 复用现有 user_id（JWT）维度，demo 中"终端用户"即登录用户（无独立访客身份基建）
- 与现有 20/min 全局限流并存：全局限流在 middleware（所有端点），对话限流在 message 端点（加严 10/min）
- 429 复用现有中间件模式，前端已处理；不落库避免污染会话
- 端点层校验（非 middleware）便于返回友好 detail 且只作用于对话端点

**Alternatives**:
- 访客 UUID：需改 Widget + 中间件链路，P2 不值。否决
- session_id：可建多会话绕过。否决
- IP：NAT 误伤/移动网络绕过。否决

## R7: RESOLVED -> CLOSED 时长

**Decision**: RESOLVED 再过 30 分钟无操作转 CLOSED（与空闲超时同窗，总 60min 从最后活动到 CLOSED）

**Rationale**:
- RESOLVED 保留 30min 供坐席/用户复核，再 CLOSED
- 与空闲窗一致，简化定时任务逻辑（同一个扫描，两段阈值）
- plan 阶段可调（写入 config 常量）

**Alternatives**:
- 立即 CLOSED：无复核窗口。否决
- 24h：demo 等不起。否决