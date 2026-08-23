# Tasks: Alembic 迁移链从零建库修复

**Feature**: 012-alembic-from-scratch-fix | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)
**Mode**: TDD(先写集成测试 -> 红 -> 实施迁移 -> 绿 -> 回归)

## Phase 1: TDD 红测试

- [X] T001 [US1-4] Write integration test `backend/tests/integration/test_alembic_from_scratch.py`(用 root 建临时库 -> subprocess 跑 `alembic upgrade head` -> inspect 比对 Base.metadata 全表全列存在 -> teardown 删库;无 MYSQL_ROOT_PASSWORD 时 skip。当前预期失败:d5 崩溃)

## Phase 2: 修迁移链(US1 - 不崩溃)

- [X] T002 [US1] Create `alembic/versions/d4b_ticket_order_base.py`(down=d4_session_msg;建 tickets 基表 tenant_id/ticket_no/title/description/status/priority/assignee_id + 公共字段,不含 phone/category;建 orders 基表 tenant_id/order_no/amount/paid_amount/status/version + 公共字段,不含 phone;建索引 ix_tickets_tenant_id/ix_tickets_ticket_no(unique)/ix_orders_tenant_id/ix_orders_order_no(unique);实现 downgrade)
- [X] T003 [US1] Re-parent `d5_ticket_order_phone.py`:down_revision `d4_session_msg` -> `d4b_ticket_order_base`(upgrade/downgrade 逻辑不变)

## Phase 3: 修 wechat_openid 重复(US4)

- [X] T004 [US4] Edit `d10_account_ext.py`:upgrade 移除 add_column wechat_openid + create_index ix_users_wechat_openid;downgrade 移除对应 drop_index/drop_column;保留 email/email_hash/phone-nullable 改动

## Phase 4: 补 6 表 + tenants 4 列(US2/US3)

- [X] T005 [US2/3] Create `alembic/versions/d11_missing_tables_tenant_cols.py`(down=merge 36e0613c0a65;建 quick_replies/refund_approvals/billing_invoices/csat_evaluations/usage_records/answer_feedback/security_incidents 7 表,列对齐模型;加 tenants widget_theme_color(String 20 NOT NULL server_default #1890ff)/widget_logo_url(String 500 nullable)/widget_welcome_text(String 500 nullable)/default_model(String 100 NOT NULL server_default deepseek/deepseek-chat);实现 downgrade)

## Phase 5: 验证(绿 + 回归)

- [X] T006 [US1-4] Run `test_alembic_from_scratch.py`(设 MYSQL_ROOT_PASSWORD=root)-> 空库 upgrade head 成功 + 全表全列存在 -> 绿
- [X] T007 验证 downgrade 可逆(空库 upgrade head 后 downgrade -1 能回退 d11)
- [X] T008 Run 全量 pytest -> 491 passed 0 failed 不回归(新测试无 root 时 skip)
- [X] T009 更新 spec/tasks 勾选 + 更新 memory `db-schema-drift-final.md`(从零建库已可 alembic 跑通)

## Dependencies

```
T001(红测试) -> T002/T003(US1) -> T004(US4) -> T005(US2/3) -> T006(绿) -> T007 -> T008
T002 必须先于 T003(d5 重接到 d4b)
T004/T005 互相独立但都须在 T006 验证前完成
```

## Notes

- 不改模型、不改 .env、不改 env.py
- 真实库已 stamp head,重接 d5 不影响其状态(已在 head,不重跑)
- d4b 建 tickets/orders 时不含 phone/category,保留 d5/d6 原语义
