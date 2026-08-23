# Data Model: LangGraph 智能体主链路骨架

**Phase 1 Output** | **Date**: 2026-08-04

## Entity: Session (MySQL)

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | 会话 ID |
| `tenant_id` | BIGINT | NOT NULL, INDEX | 所属租户 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'created' | created / routing / agent_handling / resolved / closed |
| `intent` | VARCHAR(30) | NULLABLE | 最后识别的意图 |
| `confidence` | FLOAT | NULLABLE | 最后识别的置信度 |
| `sentiment` | VARCHAR(20) | NULLABLE | 最后识别的情绪 |
| `assignee_id` | BIGINT | NULLABLE | 指派坐席 ID（Day 6 启用） |
| `created_at` | DATETIME | NOT NULL | 创建时间 |
| `updated_at` | DATETIME | NOT NULL | 最后活动时间 |

**State Machine (Day 4)**:
```
created → routing → agent_handling → resolved → closed
```

## Entity: Message (MySQL)

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | 消息 ID |
| `session_id` | BIGINT | FK→sessions.id, NOT NULL | 所属会话 |
| `role` | VARCHAR(20) | NOT NULL | user / assistant / agent / system |
| `content` | TEXT | NOT NULL | 消息文本 |
| `sources` | JSON | NULLABLE | 引用来源列表 [{"doc":"xx","chunk":0,"text":"..."}] |
| `metadata` | JSON | NULLABLE | 附加信息 {"tokens": 123, "elapsed_ms": 234} |
| `created_at` | DATETIME | NOT NULL | 发送时间 |

## Entity: AgentState (LangGraph 内存态，非持久化)

| Field | Type | Description |
|---|---|---|
| `session_id` | int | 会话 ID |
| `tenant_id` | int | 租户 ID |
| `messages` | list[dict] | 本轮上下文消息 |
| `intent` | str | 路由识别的意图 |
| `confidence` | float | 路由置信度 |
| `sentiment` | str | 情绪标签 |
| `slots` | dict | 提取的槽位键值对 |
| `retrieval_result` | list[dict] | 检索片段列表 |
| `current_agent` | str | 当前处理节点名 |
| `should_interrupt` | bool | 是否应中断转人工 |
| `final_response` | str | 最终回复文本 |

## Redis 键

| Key | Type | TTL | Description |
|---|---|---|---|
| `session:context:{id}` | JSON String | 30min | 最近 10 轮消息上下文 |

## Alembic Migration

新增 `sessions` 和 `messages` 两张表。
