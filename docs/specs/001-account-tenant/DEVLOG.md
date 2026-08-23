# 开发日志 — feature 001-account-tenant

> 智枢 ZhiHub · Day 2「账号体系 + 多租户闭环」
> 开发模式：SDD（规格驱动）+ TDD（测试先行）
> 日期：2026-08-04

---

## 一、成果概览

Day 2 三个 🔴 文件（此前均为空 stub）已全部实现，41 个任务全部落地，**55 个测试全绿**，六步端到端链路真实跑通。

| 验收点 | 结果 |
|---|---|
| 注册手机号 → 自动建 trial 租户 → 登录拿 JWT → 进后台首页 | ✅ 201 → token → home |
| 退出登录后旧 token 立即失效（黑名单） | ✅ 登出后旧 token 访问返回 401 |
| 坐席角色 token 无法调用管理员接口 | ✅ agent→billing 403，admin→200 |
| 连续 5 次密码错误锁定 15 分钟 | ✅ 锁定期内正确密码也拒绝 |
| 伪造 tenant_id 无效（以令牌为准） | ✅ query 自报 tenant_id 被忽略 |

---

## 二、SDD 流程轨迹

1. **constitution** — 从空模板提炼 5 条核心原则（分层边界 / 多租户隔离 / 资金状态防错 / 端到端验收 / 深度优先），产出 `.specify/memory/constitution.md` v1.0.0
2. **specify** — 4 用户故事 / 16 功能需求 / 6 成功标准，无 NEEDS CLARIFICATION（PRD 已写死关键规则）
3. **plan** — 技术方案 + 宪法门禁逐条 PASS，挖出 3 个实现前不可见的设计张力
4. **tasks** — 41 个 TDD 任务，按用户故事分组，测试先行
5. **implement** — 红绿循环逐层实现

---

## 三、TDD 执行轨迹（先红后绿）

| 层 | 测试文件 | 用例 |
|---|---|---|
| unit | test_validators.py / test_security.py | 25 |
| integration | test_auth_service.py / test_auth_middleware.py / test_deps.py | 24 |
| e2e | test_account_flow.py | 6 |
| **合计** | | **55 passed** |

每层均先写测试确认 FAIL，再实现转绿。

---

## 四、交付文件清单

### 新增

**SDD 规格产物**（`specs/001-account-tenant/`）
- spec.md / plan.md / research.md / data-model.md / quickstart.md
- contracts/auth-api.md / contracts/internal-interfaces.md
- checklists/requirements.md
- `.specify/memory/constitution.md`（v1.0.0）/ `.specify/feature.json`

**后端源码**
- `app/core/database.py` — 新建 DB 会话底座（engine / SessionLocal / get_db）

**测试**（`zhihub/backend/tests/`）
- conftest.py + unit/（2）+ integration/（3）+ e2e/（1）

**配置**
- requirements-dev.txt / pyproject.toml

### 修改

| 文件 | 变更 |
|---|---|
| middlewares/auth_middleware.py | 🔴 空 stub → JWT 解析 + 黑名单 + 注入身份 |
| middlewares/tenant_middleware.py | 🔴 空 stub → tenant_id 可信注入 |
| api/deps.py | 🔴 空 stub → get_current_user / require_role / require_tenant |
| services/auth_service.py | 空 stub → 注册事务 / 登录锁定 / 登出黑名单 |
| core/redis.py | 空 stub → 失败计数 + 黑名单辅助 |
| crud/user.py, crud/tenant.py | 空 stub → 数据存取 |
| schemas/auth.py, schemas/tenant.py | 空 stub → 请求/响应模型 |
| utils/validators.py | 空 stub → 手机号 / 密码强度校验 |
| api/v1/auth.py, api/v1/tenant.py | 空 stub → 路由 |
| main.py | 挂载中间件（逆序）+ 路由 |
| models/base.py | 主键改跨方言（SQLite 兼容） |
| requirements.txt | 钉 bcrypt==4.0.1（兼容修复） |

---

## 五、过程中修掉的 4 个真实问题

1. **passlib + bcrypt 5.0 不兼容** — bcrypt 哈希直接报错（passlib 1.7.4 不认识 bcrypt≥4.1 的版本接口）。修复：钉 `bcrypt==4.0.1`，写进 requirements.txt。
2. **SQLite 不认 BIGINT 自增** — SQLite 仅对 INTEGER PRIMARY KEY 自增。修复：base 模型主键改 `BigInteger().with_variant(Integer, "sqlite")`，生产 MySQL 仍是 BIGINT。
3. **SQLite `:memory:` 每连接独立库** — 建表后 session 换连接查不到表。修复：conftest 加 `StaticPool` 共享单一连接。
4. **422 状态码 deprecation** — Starlette 新版 `HTTP_422_UNPROCESSABLE_ENTITY` 改名。修复：改用字面量 422。

> 前两个是 Day 1 遗留隐患，靠静态阅读发现不了，是 TDD + 端到端验收（宪法原则 IV）跑出来的。

---

## 六、关键设计决策（对应 research.md）

- **失败计数 / 锁定用 Redis 为唯一数据源**（`login_fail:{phone}` + TTL），User 表的 `login_fail_count` / `locked_until` 两列本 feature 未用（TTL 自动解锁，无需定时任务）。
- **登出黑名单 key = `token:blocklist:{jti}`**，TTL = 令牌剩余有效期，自然过期即清理。
- **中间件职责切分**：中间件判断"是谁"（JWT + 黑名单 + 上下文注入），deps 判断"能不能做"（角色 / 租户状态）。
- **中间件注册逆序**：FastAPI `add_middleware` 后注册先执行，要 auth 先于 tenant 入站，需先注册 Tenant 再注册 Auth。

---

## 七、遗留事项 / 说明

- **T040 未跑真实 docker 容器**（环境未起 MySQL/Redis），用进程内真实 app + 内存库做了等价六步冒烟。真机演示按 quickstart.md 的 docker compose 步骤走。
- **仓库尚未纳入 git**（Three/ 目录不是 git 仓库）。如需版本化，建议 `git init` 后按下方 message 首次提交。

### 建议 commit message

```
feat(auth): 账号体系与多租户闭环 (SDD+TDD)

手机号注册→创建trial租户→登录JWT→进后台全链路，含登出黑名单、
失败锁定、接口级角色越权拦截三条安全防线。

- 中间件: JWT解析+Redis黑名单(auth) / tenant_id可信注入(tenant)
- deps: get_current_user / require_role / require_tenant
- service: 注册单事务编排 + 登录失败锁定 + 登出黑名单
- 55 tests green (unit/integration/e2e), 六步端到端链路验证

Fixes: bcrypt 5.0兼容(钉4.0.1)、SQLite BIGINT自增、:memory:连接池
```
