# Data Model: 会话生命周期与转人工增强

## 修改 MySQL 表

### tickets（加列）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| category | VARCHAR(30) | nullable, default "general" | 工单分类：general（默认）/ message（留言建单）/ 未来扩展（complaint/refund 等） |

迁移：`alembic/versions/d6_ticket_category.py`（down_revision = d5_ticket_phone）。nullable+default 保证现有工单数据兼容。

### csat_evaluations（无改动，复用）

现有字段：`session_id`(BIGINT, NOT NULL, index) / `message_id`(BIGINT, nullable) / `feedback`(VARCHAR(10), like/dislike) / `score`(INT, 1-5, nullable) / `comment`(TEXT, nullable)。

**租户隔离**：本表无 tenant_id 列，通过 `session.tenant_id` 间接隔离--CSAT 创建/查询前先校验 session 归属当前租户（宪法 II）。

## Redis 结构（非持久化表）

### `session:rounds:{session_id}` -- 会话轮次计数

```json
{"total": 3, "consecutive": 2, "last_intent": "ticket_query"}
```

| 字段 | 说明 |
|---|---|
| total | 累计总轮次，达 15 触发转人工 |
| consecutive | 连续未解决轮次：本轮 intent==last_intent 且为可解决类意图（ticket_query/order_query/sensitive_action）时 +1，否则归 0；达 3 触发转人工。知识咨询不计入 |
| last_intent | 上一轮意图，用于判定连续性 |

**TTL**: 30 分钟（与空闲超时同窗，会话活跃时每轮续期）。转人工后坐席退回 AI 时 reset consecutive=0（total 保留）。

### `agents:online:{tenant_id}` -- 在线坐席集合

Redis Set，元素 = 在线坐席 user_id。login（role=agent）SADD，logout SREM。"无在线坐席" = SCARD == 0。

### `rate:msg:{user_id}:minute` -- 对话消息限流

Redis ZSET（滑动窗口），score=timestamp，member=timestamp。每分钟清理过期成员，ZCARD 计数，达 10 拒绝。TTL 70 秒。

## 会话状态机扩展

```
CREATED -> ROUTING -> AGENT_HANDLING -> RESOLVED -> CLOSED
                    -> WAITING_HUMAN -> RESOLVED -> CLOSED
```

新增自动跃迁（cleanup_tasks 定时任务）：
- AGENT_HANDLING / WAITING_HUMAN，updated_at < now-30min -> RESOLVED
- RESOLVED，updated_at < now-60min -> CLOSED

跃迁前重新校验 updated_at（乐观条件防竞态）。

## 现有表的影响

- `sessions`：复用 status/intent/updated_at 字段，无改表。轮次计数在 Redis（不放 sessions 表，高频读写）。
- `users` / `tenant_members`：复用 role 判定（role=agent 才进在线集合），无改表。
- 退款/工单状态机/幂等：不受影响（本 feature 不动资金/写操作逻辑）。