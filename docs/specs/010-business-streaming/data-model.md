# Data Model: 业务闭环与流式输出

## 修改 MySQL 表

### knowledge_documents（加列）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| expires_at | DATETIME | nullable | 文档有效期到期时间（默认上传+90天） |
| quality_status | VARCHAR(20) | nullable, default "ready" | ready/pending_review（乱码超阈值）/expired（到期摘除） |

迁移：alembic/versions/d7_knowledge_lifecycle.py（down_revision=d6_ticket_cat）

### 退款状态机扩展

```
REFUNDING(退款中) -> REFUNDED(已退款)    [现有]
REFUNDING(退款中) -> CANCELLED(已撤销)   [新增：用户撤销]
REFUNDING(退款中) -> CANCELLED(已撤销)   [新增：超时7天自动取消]
```

大额退款（amount > 1000）：agent 角色批准后状态保持 REFUNDING（待 admin 二次审批）；admin 角色批准后 -> REFUNDED。

### billing_invoices（无改动）
现有 status: unpaid/paid。逾期降级：unpaid 且 created_at < now-15天 -> 租户 degraded。

## Redis 结构

### `billing:degraded:{tenant_id}` -- 降级标记
String "1"，TTL 24h。agent_service.handle_message 开头检查 -> 降级提示。

### `billing:quota:{tenant_id}:{month}` -- 配额计数（修改）
现有 INCR +1；改为 incr_usage(tenant_id, model) -> count += MODEL_WEIGHTS.get(model, 1.0)。

## config 新增

| 配置 | 默认 | 说明 |
|---|---|---|
| MODEL_WEIGHTS | {"deepseek/deepseek-chat": 1.0} | 模型折算权重表 |
| STORAGE_DOC_LIMIT | 50 | 租户文档数上限 |
| REFUND_TIMEOUT_DAYS | 7 | 退款超时取消天数 |
| REFUND_APPROVAL_THRESHOLD | 1000 | 大额退款审批阈值 |
| DOC_VALIDITY_DAYS | 90 | 文档有效期天数 |
| QUALITY_GARBAGE_THRESHOLD | 0.3 | 乱码率阈值 |
| INVOICE_OVERDUE_DAYS | 15 | 账单逾期降级天数 |

## SSE 事件流格式

```
data: {"chunk": "您"}\n\n
data: {"chunk": "好"}\n\n
...
data: [DONE]\n\n
```

Content-Type: text/event-stream