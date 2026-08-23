# API Contract: 账号体系 + 多租户闭环

所有接口挂在 `/api/v1` 下。错误统一返回 `{"detail": "<message>"}`（FastAPI 默认）。鉴权接口需 `Authorization: Bearer <token>`。

公开路径（免鉴权）：`POST /api/v1/auth/register`、`POST /api/v1/auth/login`、`GET /health`。

---

## POST /api/v1/auth/register — 注册并创建租户

**Request Body**
```json
{
  "phone": "13800138000",
  "password": "abc12345",
  "tenant_name": "示例科技有限公司"
}
```

**校验**：phone 手机号格式；password 强度（≥8、含字母+数字、非纯数字/连续/弱密码）；tenant_name 非空。

**201 Created**
```json
{
  "user_id": 1,
  "tenant_id": 1,
  "phone": "13800138000",
  "role": "admin",
  "plan_type": "trial"
}
```

**错误**
| 状态 | 场景 | detail |
|---|---|---|
| 409 | 手机号已注册 | "该手机号已注册，请登录或找回密码" |
| 422 | 密码强度不足 | "密码至少8位且需同时包含字母和数字，不能是常见弱密码" |
| 422 | 手机号格式错误 | "手机号格式不正确" |

**副作用**：单事务创建 user + trial tenant + admin member（失败全回滚）。

---

## POST /api/v1/auth/login — 登录签发令牌

**Request Body**
```json
{ "phone": "13800138000", "password": "abc12345" }
```

**200 OK**
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "user_id": 1,
  "tenant_id": 1,
  "role": "admin"
}
```

令牌 claims 含 sub / tenant_id / role / jti / exp（见 data-model）。

**错误**
| 状态 | 场景 | detail |
|---|---|---|
| 401 | 手机号不存在或密码错误 | "手机号或密码错误"（不区分，防枚举） |
| 423 | 账号锁定中 | "账号已锁定，请在 {n} 秒后重试" |

**副作用**：成功 → `DEL login_fail:{phone}`；失败 → `INCR`，达 5 次设 15min 锁定 TTL。

---

## POST /api/v1/auth/logout — 登出（令牌加入黑名单）

**Auth**: 需 Bearer 令牌。

**200 OK**
```json
{ "message": "已登出" }
```

**副作用**：`SET token:blocklist:{jti}` TTL=剩余有效期。此后该令牌任何请求 → 401。

**错误**
| 状态 | 场景 | detail |
|---|---|---|
| 401 | 无令牌/令牌无效/已在黑名单 | "未认证" |

---

## GET /api/v1/tenant/home — 后台首页（鉴权 + 租户上下文）

**Auth**: 需 Bearer 令牌（admin 或 agent 均可）。

**200 OK**
```json
{
  "tenant_id": 1,
  "tenant_name": "示例科技有限公司",
  "plan_type": "trial",
  "status": "active",
  "current_user": { "user_id": 1, "role": "admin" }
}
```

数据严格限定在令牌所载 `tenant_id` 范围内。

**错误**
| 状态 | 场景 | detail |
|---|---|---|
| 401 | 未认证/黑名单令牌 | "未认证" |
| 403 | 租户 closed | "租户已注销" |

---

## GET /api/v1/tenant/billing — 管理员专属接口（越权拦截样例）

用于验证接口级角色隔离（US4）。**仅 admin 可访问**。

**Auth**: 需 Bearer 令牌 + `require_role("admin")`。

**200 OK**（admin）
```json
{ "tenant_id": 1, "plan_type": "trial", "usage": {} }
```

**403 Forbidden**（agent 令牌）
```json
{ "detail": "无权访问：需要管理员角色" }
```

> 本接口在本 feature 中作为"角色隔离能力已生效"的可测样例；完整账单逻辑属 Day 6。

---

## 伪造 tenant_id 防护契约（SC-006）

任何请求即使在 body/query 携带 `tenant_id=999`，实际生效的租户过滤 MUST 以令牌 claim 为准。契约测试：用 tenant=1 的令牌请求 `/tenant/home?tenant_id=999`，返回的 `tenant_id` 必须是 1。
