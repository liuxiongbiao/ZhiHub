# Implementation Plan: Alembic 迁移链从零建库修复

**Branch**: `012-alembic-from-scratch-fix` | **Date**: 2026-08-10 | **Spec**: [spec.md](./spec.md)

## Summary

让 `alembic upgrade head` 从全新空 MySQL 库跑通且产出与模型一致的 schema。三类缺口:d5 因 tickets/orders 无建表迁移而崩溃;6 张业务表无建表迁移;tenants 缺 4 列;wechat_openid 重复添加。修复方式:插入 d4b 建 tickets/orders 基表并重接 d5;d11 head 迁移建 6 表+补 tenants 4 列;d10_account_ext 移除重复 wechat_openid。

## Technical Context

**Language/Version**: Python 3.14
**Primary Dependencies**: SQLAlchemy 2.x, Alembic, FastAPI
**Storage**: MySQL 8.0(迁移用 alter_column,MySQL 专有,不可 SQLite)
**Testing**: pytest(集成测试需 MySQL + root 建临时库,无 root 时 skip)
**Project Type**: web-service 后端
**Constraints**: 不改模型;不改 .env;迁移可逆;真实库已 stamp head 不受影响

## Constitution Check

| 原则 | 检查 | 结论 |
|---|---|---|
| I 分层边界 | 迁移属 Core/基础设施层,不碰 API/Service | ✅ 通过 |
| II 多租户隔离 | 新建表均含 tenant_id(quick_replies/refund_approvals/billing_invoices/usage_records),csat/answer_feedback 按设计通过 session/message 间接隔离(与模型一致) | ✅ 通过 |
| III 资金/状态防错 | refund_approvals/billing_invoices 建表保留 client_request_id unique 幂等键、version 乐观锁列 | ✅ 通过 |
| IV 端到端验收 | 集成测试:空库 upgrade head + inspect 全表全列存在 | ✅ 通过(SC-001/002) |
| V 深度优先 | 修迁移链是"从零建库"核心叙事的硬支撑,非边缘 | ✅ 通过 |

无宪法违背,无需 Complexity Tracking。

## Project Structure

```text
zhihub/backend/
├── alembic/
│   ├── env.py                      # 不改(settings 读 MYSQL_DATABASE)
│   └── versions/
│       ├── d4b_ticket_order_base.py  # 新建:d4 后建 tickets/orders 基表
│       ├── d5_ticket_order_phone.py  # 改:down_revision 重接到 d4b
│       ├── d10_account_ext.py        # 改:移除 wechat_openid add+index
│       └── d11_missing_tables_tenant_cols.py  # 新建:6 表 + tenants 4 列
└── tests/integration/
    └── test_alembic_from_scratch.py  # 新建:TDD 集成测试
```

## 修复方案(迁移链改动)

### 当前链(从零 upgrade 在 d5 崩溃)
```
fe03 -> d3 -> d4 -> d5(ADD tickets.phone, orders.phone)  ❌ tickets/orders 不存在
      -> d6 -> d7 -> d8 -> d9 -> [d10_account_ext, d10_audit, d10_wx_oid] -> merge
```

### 修复后链
```
fe03 -> d3 -> d4 -> d4b(建 tickets/orders 基表) -> d5(重接到 d4b)
      -> d6 -> d7 -> d8 -> d9 -> [d10_account_ext(去 wechat_openid), d10_audit, d10_wx_oid] -> merge
      -> d11(建 7 表 + tenants 4 列)  # 新 head
```

### 各迁移改动

1. **d4b_ticket_order_base(新建,down=d4_session_msg)**
   - CREATE tickets(tenant_id, ticket_no, title, description, status, priority, assignee_id + 公共字段)— 不含 phone/category
   - CREATE orders(tenant_id, order_no, amount, paid_amount, status, version + 公共字段)— 不含 phone
   - 索引:ix_tickets_tenant_id / ix_tickets_ticket_no(unique) / ix_orders_tenant_id / ix_orders_order_no(unique)

2. **d5_ticket_order_phone(改:down_revision d4_session_msg -> d4b_ticket_order_base)**
   - 仅改 down_revision,upgrade/downgrade 逻辑不变(加/删 phone)

3. **d10_account_ext(改:移除 wechat_openid)**
   - upgrade 删:add_column wechat_openid + create_index ix_users_wechat_openid
   - downgrade 删:drop_index + drop_column wechat_openid
   - 保留:phone/phone_hash nullable、email/email_hash 添加

4. **d11_missing_tables_tenant_cols(新建,down=merge 36e0613c0a65)**
   - CREATE quick_replies / refund_approvals / billing_invoices / csat_evaluations / usage_records / answer_feedback / security_incidents(7 表,列定义对齐模型)
   - ADD tenants.widget_theme_color(String 20, NOT NULL, server_default #1890ff)
   - ADD tenants.widget_logo_url(String 500, nullable)
   - ADD tenants.widget_welcome_text(String 500, nullable)
   - ADD tenants.default_model(String 100, NOT NULL, server_default deepseek/deepseek-chat)

## TDD 策略

- **红**:先写 `test_alembic_from_scratch.py` — 空库 upgrade head + inspect 全表全列存在。当前 d5 崩溃 -> 测试失败。
- **绿**:实施 4 项迁移改动 -> 测试通过。
- **回归**:全量 pytest 491 passed 不回归(新测试无 root 时 skip)。
- 测试用 root 建临时库 `zhihub_alembic_test_<pid>`,subprocess 跑 alembic upgrade(经 env.py 读 MYSQL_DATABASE env),inspect 比对,teardown 删库。无 MYSQL_ROOT_PASSWORD 时 skip。
