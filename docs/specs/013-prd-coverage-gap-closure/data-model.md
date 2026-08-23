# Data Model: PRD 覆盖缺口闭环

## 实体变更

### Order（订单）-- 新增字段 + 发货操作

| 字段 | 类型 | 说明 |
|---|---|---|
| tracking_no | String(100), nullable | 物流单号，发货时写入 |

**状态机跃迁（既有，本 feature 启用）**:
```
created -> paid -> shipped -> completed
                 -> refunding -> refunded | paid(拒绝退款回退)
created -> cancelled
```
- 发货操作: `paid -> shipped`（已存在于 VALID_TRANSITIONS），写入 tracking_no，乐观锁 version+1。
- 非法跃迁（如 shipped->shipped、created->shipped）-> InvalidTransition -> 400。

### RegisterRequest（注册请求）-- 新增字段

| 字段 | 类型 | 说明 |
|---|---|---|
| code | str, 必填 | 短信验证码，register() 先校验 |

校验: 复用 Redis `sms_code:{phone}`，TTL 内匹配，无效抛 InvalidCode -> 401。

## 既有实体（本 feature 仅对接，不改）

| 实体 | 用途 | 关键约束 |
|---|---|---|
| CSAT | 会话满意度评价 | score 1-5，一会话一条（submit_csat 内去重，重复 409） |
| QuickReply | 快捷回复模板 | 租户隔离（tenant_id），content + shortcut |
| AuditLog | 审计日志 | 租户隔离，关键操作自动写入 |
| RefundApproval | 退款审批 | 状态 pending/approved/rejected/refunding/refunded/cancelled |
| Tenant | 租户 | widget_theme_color/logo_url/welcome_text；seat_limit 约束邀请 |

## 关系图（本 feature 涉及）

```
Tenant 1--* TenantMember（seat_limit 约束 invite）
Session 1--1 CSAT（closed 后提交，一会话一条）
Session 1--* Ticket（leave-message 建单，category=message）
Order 1--* RefundApproval
Order.tracking_no（发货写入）
```
