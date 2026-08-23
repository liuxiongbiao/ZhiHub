# Phase 0 Research: 账号体系 + 多租户闭环

本文件解决 plan 中的技术未知点与既有骨架的设计张力。每项含：决策 / 理由 / 备选。

---

## R1. 登录失败计数与锁定：存 DB 还是 Redis？

**背景张力**：既有 `models/user.py` 已有 `login_fail_count` / `locked_until` 两列；而 PRD V1.4 明确规定用 Redis `login_fail:{account}` key + 15 分钟 TTL。

**Decision**：以 **Redis 为准**（`login_fail:{phone}`，值为失败次数，达 5 次时对该 key 设 15 分钟 TTL 表示锁定）。DB 的 `login_fail_count` / `locked_until` 两列在本 feature **不写入、不读取**，视为历史遗留，注释标注"锁定状态以 Redis 为准，此列保留待审计需求"。

**Rationale**：
- TTL 自动过期 = 锁定自动解除，无需定时任务清理，这正是 PRD 选 Redis 的原因。
- 失败计数是高频写的易失状态，放 DB 会产生大量无谓写与行锁竞争。
- 宪法原则 IV 要求"锁定可被负向测试稳定复现"，Redis + TTL 的行为比 DB 时间比较更易在测试中控制（可直接读 key/TTL 断言）。

**Alternatives considered**：
- 纯 DB 方案：需自己实现"锁定是否到期"的时间比较与解锁，且并发失败计数要加行锁；被 TTL 方案的简洁性击败。
- 双写 DB+Redis：违反单一数据源，增加不一致风险，无收益。

**并发安全**：失败计数用 Redis `INCR`（原子），首次失败时 `EXPIRE` 设一个滚动窗口；命中 5 次时将 key TTL 重设为 15 分钟锁定期。读判定"是否锁定"= key 存在且值 ≥ 5 且仍在锁定 TTL 内。

---

## R2. DB 会话基础设施缺失：如何提供 get_db？

**背景**：全项目 grep 不到 `create_engine`/`SessionLocal`/`sessionmaker`，`deps.py` 要求的 `get_db()` 没有底座。

**Decision**：新建 `app/core/database.py`，提供**同步** SQLAlchemy 2.0 `engine` + `SessionLocal`（`sessionmaker`）+ `get_db()` 生成器依赖。连接串从 `settings` 的 mysql_* 拼装（`pymysql` 驱动，已在 requirements）。

**Rationale**：
- requirements 里是 `pymysql`（同步驱动），非 `aiomysql`/`asyncpg`，既有技术选型指向同步 SQLAlchemy。用同步 Session 匹配现状，避免引入 async 驱动的额外依赖与复杂度（YAGNI，宪法复杂度约束）。
- FastAPI 路由可为 `def`（同步）由线程池执行，或 `async def` 中调用同步 Session；本 feature 采用同步路由 + `Depends(get_db)`，简单直接。
- 事务边界：`get_db()` 每请求一个 Session，`auth_service` 注册流程在该 Session 内显式 `commit()`/`rollback()`，保证"用户+租户+成员"原子。

**Alternatives considered**：
- async SQLAlchemy + aiomysql：性能更优但需改驱动依赖、全链路 async，超出 Day 2 裁剪范围，拒绝。

---

## R3. 中间件 vs 依赖注入：鉴权职责如何切分？

**背景**：FastAPI 有两种拦截点——ASGI 中间件（全局、请求级）和 `Depends`（路由级）。宪法原则 I 要求"是谁"归中间件、"能不能做"归 deps。

**Decision**：
- **`auth_middleware`（ASGI 中间件）**：对非公开路径解析 `Authorization: Bearer`，验签 + 验过期 + **查 Redis 黑名单**，通过则把解析出的 claims（user_id/tenant_id/role/jti/exp）写入 `request.state`；失败则直接返回 401（不进入路由）。公开路径白名单：`/health`、`/api/v1/auth/register`、`/api/v1/auth/login`、`/docs`、`/openapi.json`。
- **`tenant_middleware`（ASGI 中间件）**：在 auth 之后，从 `request.state` 取 `tenant_id` 注入 `request.state.tenant_id`（隔离防线单点）；**只搬运可信来源，不读请求体/查询参数的 tenant_id**。
- **`deps.py`（Depends）**：`get_current_user` 从 `request.state` + DB 加载 User 实体（校验用户仍存在/有效）；`require_role(*roles)` 校验角色白名单，不符返回 403；`require_tenant()` 校验租户状态（active/readonly/closed）。

**Rationale**：中间件不查库做业务判断（原则 I：中间件禁含业务逻辑），只做无状态的令牌校验与上下文搬运；"用户是否还有效""角色够不够""租户什么状态"是业务判断，落在 deps/service 边界。这样越权拦截（US4）天然落在 `require_role`，可独立测试。

**关键顺序**：FastAPI `add_middleware` 注册顺序与执行顺序**相反**（后注册的先执行入站）。要让 auth 先于 tenant 执行入站，注册顺序 MUST 为：先 `add_middleware(tenant)` 再 `add_middleware(auth)`（配合 main.py 头注释的逻辑顺序 access_log→auth→tenant→rate_limit，注册时按逆序写）。research 明确此点以防实现踩坑。

**Alternatives considered**：
- 全部用 Depends 做鉴权：黑名单查询会散落到每个路由，且无法在进入路由前统一拦截；违反"中间件负责是谁"的分层，拒绝。
- 全部用中间件（含角色校验）：中间件会塞入业务判断，违反原则 I，拒绝。

---

## R4. 登出令牌黑名单的键与 TTL

**Decision**：黑名单 key = `token:blocklist:{jti}`（用 JWT 的 `jti` 唯一标识，非整段 token，更短且不泄露 token）。登出时读取当前 token 的 `exp`，TTL = `exp - now`（剩余有效期，秒）；若已过期则无需写入。鉴权查黑名单 = `EXISTS token:blocklist:{jti}`。

**Rationale**：`security.py` 生成的 JWT 已含 `jti`（`secrets.token_hex(16)`），天然适合做黑名单键。TTL 对齐剩余有效期，令牌自然过期后 key 自动清理，零维护。

**Alternatives considered**：以整段 token 为 key——更长、可能在日志/Redis 中暴露完整凭证，拒绝。

---

## R5. 密码强度校验规则落点

**Decision**：`utils/validators.py` 提供纯函数 `validate_password_strength(pw) -> None|raise` 与 `validate_phone(phone)`。规则（PRD）：长度 ≥ 8、同时含字母和数字、拒绝纯数字、拒绝连续字符（如 `12345678`、`abcdefgh`）、拒绝常见弱密码表（`password`、`88888888` 等）。`auth_service` 注册前调用。

**Rationale**：纯函数无副作用、易单元测试（TDD 首批红灯测试之一），与业务编排解耦。

---

## R6. TDD 测试隔离策略

**Decision**：
- DB：`conftest.py` 用 **SQLite in-memory**（`sqlite:///:memory:`）+ `Base.metadata.create_all` 建表，每个测试用事务回滚夹具隔离。对 MySQL 特有类型（`BigInteger`/`Numeric`）SQLite 兼容，够用。
- Redis：用 `fakeredis` 提供内存 Redis，支持 `INCR`/`EXPIRE`/`TTL`/`EXISTS`，覆盖失败计数与黑名单测试。
- App：`TestClient(app)` 覆盖中间件全链路（e2e 层验证注册→登录→越权真实经过中间件）。
- Token 工厂夹具：生成 admin/agent/过期/黑名单四类 token 供 deps 与 middleware 测试复用。

**Rationale**：in-memory + fake 依赖让测试无需真实中间件即可跑，满足 TDD 快速红绿循环；e2e 层用 TestClient 确保中间件顺序与 deps 组合被真实验证（原则 IV）。

**新增测试依赖**（写入 requirements 或 dev 依赖）：`pytest`、`pytest-asyncio`、`fakeredis`、`httpx`（已有）。

---

## 决策汇总

| 编号 | 决策 |
|---|---|
| R1 | 失败计数/锁定以 Redis `login_fail:{phone}` + TTL 为唯一数据源，DB 两列本 feature 不用 |
| R2 | 新建 `core/database.py`：同步 SQLAlchemy engine + SessionLocal + get_db |
| R3 | auth/tenant 中间件只做"是谁"+上下文搬运；deps 做"能不能做"；注册顺序需逆序 |
| R4 | 黑名单 key=`token:blocklist:{jti}`，TTL=剩余有效期 |
| R5 | 密码/手机号校验为 validators 纯函数，service 调用 |
| R6 | 测试用 SQLite in-memory + fakeredis + TestClient，四层测试 |

所有 NEEDS CLARIFICATION 已解决，可进入 Phase 1。
