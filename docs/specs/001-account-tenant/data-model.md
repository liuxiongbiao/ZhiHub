# Phase 1 Data Model: 账号体系 + 多租户闭环

关系型实体已在 `zhihub/backend/app/models/` 定稿（Day 1）；本文件记录本 feature 视角的实体契约、Redis 键设计、状态取值与校验规则。

## 关系型实体（MySQL）

### User（`users`）— 已存在

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BigInteger | PK, autoincrement | 主键 |
| phone | String(20) | UNIQUE, NOT NULL, index | 登录名，全系统唯一 |
| password_hash | String(255) | NOT NULL | bcrypt 哈希（由 `security.hash_password` 产出） |
| role_default | String(20) | NOT NULL, default `agent` | 全局默认角色；租户内实际角色以 TenantMember.role 为准 |
| login_fail_count | Integer | default 0 | **本 feature 不使用**（锁定状态以 Redis 为准，见 research R1） |
| locked_until | DateTime | nullable | **本 feature 不使用**（同上） |
| created_at / updated_at | DateTime | 基类 | |

**校验规则**：
- 注册时 phone 必须唯一（重复 → 业务错误"已注册"）。
- password 明文入库前必过 `validate_password_strength`；仅存哈希，绝不存明文。

### Tenant（`tenants`）— 已存在

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BigInteger | PK | 即 `tenant_id`，贯穿全系统的隔离维度 |
| name | String(255) | NOT NULL | 企业名，**允许重复**（不做唯一约束） |
| plan_type | String(20) | NOT NULL, default `trial` | trial / basic / pro / enterprise |
| quota_conversations_per_month | Integer | default 0 | 本 feature 不涉及计量 |
| seat_limit | Integer | default 1 | **trial 默认 1**（管理员本人兼坐席，符合 PRD） |
| status | String(20) | NOT NULL, default `active` | active / readonly / closed |
| refund_approval_threshold | Numeric(10,2) | default 0 | 本 feature 不涉及 |
| created_at / updated_at | DateTime | 基类 | |

**注册时置值**：`plan_type='trial'`、`seat_limit=1`、`status='active'`。

### TenantMember（`tenant_members`）— 已存在

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BigInteger | PK | |
| tenant_id | BigInteger | FK→tenants.id, NOT NULL | |
| user_id | BigInteger | FK→users.id, NOT NULL | |
| role | String(20) | NOT NULL, default `agent` | admin / agent |
| — | — | UNIQUE(tenant_id, user_id) | 同一用户在同一租户不可重复 |

**注册时**：创建一条 `role='admin'` 的关联（注册者即租户管理员）。

## 实体关系

```
User (1) ──< TenantMember >── (1) Tenant
                  │
              role: admin|agent
```

一个 User 可通过多条 TenantMember 属于多个租户；本 feature 注册路径只建立"1 User ↔ 1 新 Tenant（admin）"。

## 事务边界（注册）

注册在**单个 DB Session 事务**内顺序执行，任一步失败整体回滚（FR-002，SC-001）：

1. 校验 phone 未注册（查 `users`）
2. `INSERT users`（password_hash，role_default='admin'）
3. `INSERT tenants`（trial/seat_limit=1/active）
4. `INSERT tenant_members`（tenant_id, user_id, role='admin'）
5. `commit()`；异常 → `rollback()`，不留孤儿数据

## Redis 键设计

| 键 | 类型 | 值 | TTL | 用途 |
|---|---|---|---|---|
| `login_fail:{phone}` | String(int) | 连续失败次数 | 锁定期 15 分钟（达阈值时设置） | 登录失败计数与锁定（R1） |
| `token:blocklist:{jti}` | String | `"1"` 占位 | 令牌剩余有效期（exp−now 秒） | 登出黑名单（R4） |

**失败计数状态机**：
- 密码错 → `INCR login_fail:{phone}`；若为首次（值=1）设一个滚动窗口 TTL。
- 值达 5 → 重设该 key TTL = 900s（15 分钟锁定）。
- 判定锁定 = key 存在且值 ≥ 5。
- 登录成功 → `DEL login_fail:{phone}`（清零）。
- 锁定期内即使密码正确也拒绝，并返回剩余 TTL 秒数用于提示（FR-007，SC-003）。

## 请求上下文（request.state）注入契约

| 字段 | 来源 | 注入者 | 消费者 |
|---|---|---|---|
| `state.user_id` | JWT `sub` | auth_middleware | deps.get_current_user |
| `state.role` | JWT claim `role` | auth_middleware | deps.require_role |
| `state.jti` | JWT `jti` | auth_middleware | （黑名单已在中间件校验） |
| `state.tenant_id` | JWT claim `tenant_id` | tenant_middleware | 下游所有租户过滤查询 |

**红线**：`state.tenant_id` 只允许来自 JWT，中间件 MUST NOT 读取请求体/查询参数中的 tenant_id（FR-010，SC-006）。

## JWT Claims 契约

`create_access_token(subject=str(user_id), extra_claims={...})` 中 extra_claims：

| claim | 值 | 说明 |
|---|---|---|
| sub | str(user_id) | 用户主键 |
| tenant_id | int | 当前登录所属租户 |
| role | "admin"\|"agent" | 该租户内角色（取自 TenantMember.role） |
| jti | hex(16) | security 自动生成，黑名单键 |
| iat / exp | 时间戳 | security 自动生成 |

## 角色与状态取值表

| 维度 | 取值 | 本 feature 行为 |
|---|---|---|
| role | admin / agent | require_role 白名单校验；agent 调管理员接口 → 403 |
| tenant.status | active / readonly / closed | require_tenant：active 放行；readonly 放行读、拒绝写；closed 全拒 |
| plan_type | trial / basic / pro / enterprise | 注册固定 trial |
