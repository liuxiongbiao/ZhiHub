# Feature Specification: Alembic 迁移链从零建库修复

**Feature Branch**: `012-alembic-from-scratch-fix`

**Created**: 2026-08-10

**Status**: Done

**Input**: User description: "补写 phone 列的 alembic 迁移(解决从零建库缺列隐患)"。经核查 phone 列 d9 已覆盖,真实隐患是 alembic upgrade head 从空库跑不通。

## 问题根因(核查结论)

从零 `alembic upgrade head` 在 d5 崩溃:`Table 'tickets' doesn't exist`。核查迁移链发现三类缺口:

1. **8 张表无 CREATE TABLE 迁移**:tickets / orders / quick_replies / refund_approvals / billing_invoices / csat_evaluations / usage_records / answer_feedback(靠 `init_db create_all` 兜底,alembic 链未覆盖)
2. **tenants 缺 4 列**:widget_theme_color / widget_logo_url / widget_welcome_text / default_model(模型有,迁移无)
3. **wechat_openid 重复添加**:d10_account_ext(String 64 非 unique)与 d10_wx_oid(String 128 unique)都加这列,从零 upgrade 会 duplicate column

phone 列本身 d9 已迁移到 String(255),与模型一致,非缺口。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 从零 upgrade 不崩溃 (Priority: P1)

d5 给 tickets/orders 加 phone 列,但这俩表从未被迁移创建。需在 d4 与 d5 之间插入建表迁移,并把 d5 重接到它后面,使迁移链线性可跑。

**Why this priority**:这是从零建库的直接阻塞点,d5 崩溃则后续全部迁移无法执行。

**Independent Test**:空库跑 `alembic upgrade head`,d5/d6/d9 不报 `Table doesn't exist`。

**Acceptance Scenarios**:
1. **Given** 全新空库,**When** `alembic upgrade head`,**Then** 不抛 `Table 'tickets' doesn't exist`。
2. **Given** d4b 建出 tickets/orders 基表(无 phone/category),**When** d5 执行,**Then** 成功加 phone 列。
3. **Given** 迁移链重接后,**When** `alembic current` 指向 head,**Then** 线性无分叉(d4b 之前的分叉保留)。

---

### User Story 2 - 从零 upgrade 后无缺表 (Priority: P1)

6 张业务表(quick_replies / refund_approvals / billing_invoices / csat_evaluations / usage_records / answer_feedback)无建表迁移。在 head 后追加一个迁移一次性建齐。

**Why this priority**:缺表则对应 CRUD/Service 无法运行,从零建库的库不完整。

**Independent Test**:空库 upgrade 后,`inspect(engine).has_table("quick_replies")` 等 6 张表全部为 True。

**Acceptance Scenarios**:
1. **Given** 全新空库 upgrade head,**When** 检查 6 张表,**Then** 全部存在。
2. **Given** 各表建表,**When** 检查列,**Then** 与模型定义一致(tenant_id/隔离键/业务字段齐全)。

---

### User Story 3 - 从零 upgrade 后 tenants 无缺列 (Priority: P2)

tenants 的 widget_theme_color / widget_logo_url / widget_welcome_text / default_model 4 列模型有、迁移无。随 US2 的 head 迁移一并补上(server_default 兼容已有行)。

**Why this priority**:widget 品牌定制与默认模型是 PRD 3.8/3.9 字段,从零建库会缺。

**Independent Test**:空库 upgrade 后 tenants 表含这 4 列,且 NOT NULL 列有 server_default。

**Acceptance Scenarios**:
1. **Given** 全新空库 upgrade head,**When** 检查 tenants 列,**Then** 含 widget_theme_color(String 20, default #1890ff)/widget_logo_url(String 500, nullable)/widget_welcome_text(String 500, nullable)/default_model(String 100, default deepseek/deepseek-chat)。

---

### User Story 4 - wechat_openid 不重复添加 (Priority: P1)

d10_account_ext 与 d10_wx_oid 都加 wechat_openid。模型(user.py)最终定义为 String(128) unique(d10_wx_oid 版本)。从 d10_account_ext 移除 wechat_openid 的 add_column/index,保留其 email/email_hash/phone-nullable 改动。

**Why this priority**:重复添加会令从零 upgrade 在 d10 分支报 duplicate column,是 P1 阻塞。

**Independent Test**:空库 upgrade 到 head,users.wechat_openid 存在且为 String(128) unique,无重复列错误。

**Acceptance Scenarios**:
1. **Given** d10_account_ext 移除 wechat_openid 后,**When** 从零 upgrade 跑过 merge,**Then** 无 duplicate column 错误。
2. **Given** upgrade 完成,**When** 检查 users.wechat_openid,**Then** 存在、String(128)、unique index。

---

### Edge Cases

- 已 stamp head 的真实库:重接 d5 不影响(已在 head,upgrade 不会重跑);真实库已有表/列(init_db + 手动 ALTER),不冲突。
- downgrade 可逆性:d4b/d11 须实现 downgrade(删表/删列),d10_account_ext 移除项的 downgrade 同步移除。
- server_default:NOT NULL 新列(widget_theme_color/default_model)须带 server_default,否则已有行(真实库)插入失败。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `alembic upgrade head` 从全新空 MySQL 库开始 MUST 成功跑到 head,不抛异常。
- **FR-002**: upgrade 后的 schema 与 `Base.metadata` MUST 无缺表/缺列(每张模型表的每列都在 DB 中存在)。
- **FR-003**: tickets/orders 基表 MUST 在 d5 之前由迁移创建(d4b),d5/d6/d9 保持原语义(加 phone/category/扩容)。
- **FR-004**: 6 张缺失表 MUST 由新 head 迁移建出,列定义与模型一致。
- **FR-005**: tenants 4 列 MUST 由新 head 迁移补齐,NOT NULL 列带 server_default。
- **FR-006**: wechat_openid MUST 只被添加一次(String 128 unique),d10_account_ext 不再添加。
- **FR-007**: 所有新迁移 MUST 实现 downgrade。

### Key Entities

- **tickets / orders**:d4b 建基表(不含 phone/category,由 d5/d6 增补)。
- **quick_replies / refund_approvals / billing_invoices / csat_evaluations / usage_records / answer_feedback**:d11 新建。
- **tenants**:d11 补 4 列(widget 主题/Logo/欢迎语/默认模型)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 全新空库 `alembic upgrade head` 退出码 0,无异常。
- **SC-002**: upgrade 后 `inspect` 检查 Base.metadata 全部表+列存在,断言通过(回归测试)。
- **SC-003**: 全量 pytest 保持 491 passed 0 failed(新测试为集成测试,无 root 时 skip)。
- **SC-004**: `alembic downgrade` 可回退新迁移(可逆)。

## Assumptions

- 项目非生产(stamp head 为主),允许改动现有迁移文件(d5 重接、d10_account_ext 去重)。
- 真实库已 stamp head 且表/列齐全(init_db + 手动 ALTER),重接不影响其状态。
- 从零建库指 `alembic upgrade head` 自身可跑通,不依赖 `init_db create_all`。
- MySQL 8.0,docker-compose 提供 root/root 凭据用于集成测试建临时库。
