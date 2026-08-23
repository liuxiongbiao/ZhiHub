---
description: "Task list for 账号体系 + 多租户闭环 (TDD mode)"
---

# Tasks: 账号体系 + 多租户闭环

**Input**: Design documents from `specs/001-account-tenant/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: TDD 已被明确要求 —— 每个用户故事的测试任务 MUST 先写并确认 FAIL,再实现。

**Organization**: 按用户故事分组。所有路径基于 `zhihub/backend/`(plan.md 结构决策)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行(不同文件、无未完成依赖)
- **[Story]**: US1/US2/US3/US4,对应 spec.md 用户故事
- 每个任务含精确文件路径

## Path Conventions

后端根 = `zhihub/backend/`。源码在 `app/`,测试在 `tests/`。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 测试底座与依赖就绪

- [ ] T001 在 `zhihub/backend/requirements.txt` 追加 dev 测试依赖注释块并新建 `zhihub/backend/requirements-dev.txt`(pytest, pytest-asyncio, fakeredis)
- [ ] T002 [P] 创建测试根结构:`zhihub/backend/tests/__init__.py`、`tests/unit/`、`tests/integration/`、`tests/e2e/` 各含 `__init__.py`
- [ ] T003 [P] 在 `zhihub/backend/pyproject.toml` 添加 `[tool.pytest.ini_options]`(testpaths、asyncio_mode=auto)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有用户故事都依赖的基础设施 —— DB 会话、Redis 辅助、测试夹具、校验器

**⚠️ CRITICAL**: 本阶段完成前,任何用户故事不能开始

### Tests First (RED)

- [ ] T004 [P] 在 `tests/unit/test_validators.py` 写手机号格式 + 密码强度测试(≥8位、含字母数字、拒纯数字/连续字符 `12345678`/弱密码 `password`),确认 FAIL
- [ ] T005 [P] 在 `tests/unit/test_security.py` 写 JWT 生成/解码 + bcrypt 校验 + 过期令牌解码返回 None 测试,确认 FAIL(security.py 已实现,此为回归保护)

### Implementation (GREEN)

- [ ] T006 新建 `zhihub/backend/app/core/database.py`:同步 SQLAlchemy engine + SessionLocal + `get_db()` 生成器(连接串取自 settings.mysql_*,pymysql 驱动)—— 见 research R2
- [ ] T007 [P] 实现 `zhihub/backend/app/utils/validators.py`:`validate_phone` + `validate_password_strength`(纯函数,不合法 raise ValueError)—— 使 T004 转绿
- [ ] T008 补充 `zhihub/backend/app/core/redis.py`:连接池 `get_redis()` + 登录失败计数辅助(`incr_login_fail`/`get_login_fail`/`lock_account`/`get_lock_ttl`/`clear_login_fail`)+ 黑名单辅助(`blocklist_token`/`is_blocklisted`)—— 见 research R1/R4 与 internal-interfaces 契约
- [ ] T009 新建 `zhihub/backend/tests/conftest.py` 夹具:SQLite in-memory + `Base.metadata.create_all`、事务回滚隔离、fakeredis 注入、`TestClient(app)`、token 工厂(admin/agent/过期/黑名单四类)—— 见 research R6
- [ ] T010 [P] 实现 `zhihub/backend/app/schemas/auth.py`:RegisterRequest/RegisterResponse/LoginRequest/LoginResponse(pydantic v2)—— 对齐 contracts/auth-api.md
- [ ] T011 [P] 实现 `zhihub/backend/app/schemas/tenant.py`:TenantHomeResponse/CurrentUser —— 对齐 contracts/auth-api.md

**Checkpoint**: DB 会话、Redis 辅助、校验器、测试夹具就绪,T004/T005 转绿

---

## Phase 3: User Story 1 - 企业管理员注册并开通租户 (Priority: P1) 🎯 MVP

**Goal**: 手机号注册在单事务内创建 user + trial 租户 + admin 成员,失败全回滚

**Independent Test**: `POST /auth/register` 全新手机号 → 201 且三表各新增一条;中途失败无孤儿数据

### Tests for User Story 1 (RED) ⚠️ 先写,确认 FAIL

- [ ] T012 [P] [US1] 在 `tests/integration/test_auth_service.py` 写注册成功(建 user+trial tenant+admin member)与事务回滚(建租户失败→user 不残留)测试
- [ ] T013 [P] [US1] 在 `tests/integration/test_auth_service.py` 写重复手机号注册拒绝、弱密码拒绝测试(与 T012 同文件,追加用例)
- [ ] T014 [P] [US1] 在 `tests/e2e/test_account_flow.py` 写注册端点契约测试(201 响应体字段 + 409/422 错误)—— 对齐 contracts

### Implementation for User Story 1 (GREEN)

- [ ] T015 [P] [US1] 实现 `zhihub/backend/app/crud/user.py`:`get_by_phone` + `create` —— 只存取,无业务判断
- [ ] T016 [P] [US1] 实现 `zhihub/backend/app/crud/tenant.py`:`create_tenant`(trial/seat_limit=1/active)+ `add_member` + `get_by_id`
- [ ] T017 [US1] 实现 `zhihub/backend/app/services/auth_service.py::register`:校验强度→查重→单事务建三表→commit/rollback,定义 PhoneAlreadyExists/WeakPassword 异常(依赖 T007/T015/T016)
- [ ] T018 [US1] 实现 `zhihub/backend/app/api/v1/auth.py` 的 `POST /register` 路由:`Depends(get_db)`,调 auth_service,映射异常到 409/422(依赖 T017)
- [ ] T019 [US1] 在 `zhihub/backend/app/main.py` 挂载 auth 路由 `app.include_router(auth.router, prefix="/api/v1/auth")`

**Checkpoint**: 注册链路独立可测,T012-T014 转绿,MVP 可演示

---

## Phase 4: User Story 2 - 登录获取访问凭证并进入后台 (Priority: P1)

**Goal**: 手机号+密码登录签发 JWT;失败计数达 5 锁定 15 分钟;携带令牌进后台首页

**Independent Test**: 注册账号登录 → 200+token;token 访问 `/tenant/home` → 200;连续 5 次错误 → 423

### Tests for User Story 2 (RED) ⚠️

- [ ] T020 [P] [US2] 在 `tests/integration/test_auth_service.py` 写登录成功签发令牌(claims 含 tenant_id/role)、密码错误累计、连续 5 次锁定 15 分钟、锁定期内正确密码仍拒绝测试
- [ ] T021 [P] [US2] 在 `tests/integration/test_auth_middleware.py` 写有效令牌放行、无令牌/过期令牌 401、公开路径免鉴权、伪造 tenant_id 被忽略(SC-006)测试
- [ ] T022 [P] [US2] 在 `tests/integration/test_deps.py` 写 `get_current_user` 加载用户、用户不存在→401、`require_tenant` 校验 closed→403 测试
- [ ] T023 [P] [US2] 在 `tests/e2e/test_account_flow.py` 写 注册→登录→GET /tenant/home 全链路(tenant_id 一致)测试

### Implementation for User Story 2 (GREEN)

- [ ] T024 [US2] 实现 `zhihub/backend/app/services/auth_service.py::login`:查锁定(Redis)→校验密码→失败 INCR/达阈值锁定→成功清零+取 admin member 角色+签 JWT,定义 AccountLocked/InvalidCredentials 异常(依赖 T008)
- [ ] T025 [US2] 实现 `zhihub/backend/app/middlewares/auth_middleware.py`:公开路径放行、Bearer 解析、验签验过期、查黑名单、注入 `request.state.user_id/role/jti`(依赖 T008,🔴)
- [ ] T026 [US2] 实现 `zhihub/backend/app/middlewares/tenant_middleware.py`:从 state claims 注入 `request.state.tenant_id`,绝不读 body/query(依赖 T025,🔴)
- [ ] T027 [US2] 实现 `zhihub/backend/app/api/deps.py::get_current_user` + `require_tenant`(依赖 T006/T025/T026,🔴)
- [ ] T028 [US2] 实现 `zhihub/backend/app/api/v1/auth.py::POST /login` 路由,映射异常 401/423(依赖 T024)
- [ ] T029 [US2] 实现 `zhihub/backend/app/api/v1/tenant.py::GET /home` 路由 + 在 `main.py` 挂载 tenant 路由与按逆序注册 Auth/Tenant 中间件(依赖 T025/T026/T027,注册顺序见 internal-interfaces)

**Checkpoint**: 登录+鉴权+进后台链路独立可测,T020-T023 转绿

---

## Phase 5: User Story 3 - 登出即时失效 (Priority: P2)

**Goal**: 登出将令牌 jti 写入 Redis 黑名单(TTL=剩余有效期),此后该令牌全部请求 401

**Independent Test**: 登录→登出→用同一 token 访问 `/tenant/home` → 401

### Tests for User Story 3 (RED) ⚠️

- [ ] T030 [P] [US3] 在 `tests/integration/test_auth_service.py` 写 `logout` 写黑名单(jti + 正确 TTL)测试
- [ ] T031 [P] [US3] 在 `tests/e2e/test_account_flow.py` 写 登录→登出→旧 token 访问被拒 401 测试(SC-004)

### Implementation for User Story 3 (GREEN)

- [ ] T032 [US3] 实现 `zhihub/backend/app/services/auth_service.py::logout`:`blocklist_token(jti, ttl=exp-now)`(依赖 T008)
- [ ] T033 [US3] 实现 `zhihub/backend/app/api/v1/auth.py::POST /logout` 路由:从 `request.state.jti`+exp 调 logout(依赖 T032,黑名单查询已在 T025 中间件覆盖)

**Checkpoint**: 登出即时失效可测,T030-T031 转绿

---

## Phase 6: User Story 4 - 接口级角色权限隔离 (Priority: P2)

**Goal**: `require_role("admin")` 拦截坐席令牌访问管理员专属接口(403),admin 放行

**Independent Test**: agent 令牌 → `/tenant/billing` → 403;admin 令牌 → 200

### Tests for User Story 4 (RED) ⚠️

- [ ] T034 [P] [US4] 在 `tests/integration/test_deps.py` 写 `require_role` 白名单校验:agent 调管理员接口→403、admin→放行测试(SC-005)
- [ ] T035 [P] [US4] 在 `tests/e2e/test_account_flow.py` 写 坐席令牌访问 `/tenant/billing` 返回 403 端到端测试

### Implementation for User Story 4 (GREEN)

- [ ] T036 [US4] 实现 `zhihub/backend/app/api/deps.py::require_role(*roles)` 依赖工厂:校验 `request.state.role`,不符 raise 403(依赖 T027,🔴)
- [ ] T037 [US4] 实现 `zhihub/backend/app/api/v1/tenant.py::GET /billing` 样例接口,加 `Depends(require_role("admin"))`(依赖 T036)

**Checkpoint**: 越权拦截可测,T034-T035 转绿;四条用户故事全部独立可用

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 收尾、回归、验收

- [ ] T038 运行 `pytest` 全量,确认四层测试全绿(unit/integration/e2e)
- [ ] T039 [P] 运行 quickstart.md 的关键负向用例 `pytest -k "lockout or blocklist or forbidden"` 确认稳定复现
- [ ] T040 按 quickstart.md「手动端到端演示」用 docker compose + alembic + uvicorn 走注册→登录→home→logout→越权六步,确认无报错
- [ ] T041 [P] 复核三个 🔴 文件(auth_middleware/tenant_middleware/deps)逻辑,确保可逐行讲解;移除多余注释

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)**: 无依赖,立即开始
- **Foundational (P2)**: 依赖 Setup;**阻塞所有用户故事**(DB 会话、Redis、校验器、夹具是共享地基)
- **US1 (P3)**: 依赖 Foundational
- **US2 (P4)**: 依赖 Foundational;登录复用 US1 的 crud,但可独立测试。中间件/deps 在此阶段建立
- **US3 (P5)**: 依赖 US2(需先有登录令牌与 auth_middleware 黑名单查询)
- **US4 (P6)**: 依赖 US2(需先有 deps.get_current_user 与 require_tenant 底座)
- **Polish (P7)**: 依赖全部用户故事

### Within Each User Story (TDD)

- 测试先写并 FAIL → 再实现转绿 → 重构
- crud/model 先于 service;service 先于 endpoint;中间件先于依赖它的 deps

### Parallel Opportunities

- Setup: T002/T003 并行
- Foundational 测试:T004/T005 并行;实现 T007/T010/T011 并行(不同文件)
- US1 测试:T012/T013/T014 并行;crud T015/T016 并行
- US2 测试:T020/T021/T022/T023 并行
- US3/US4 测试各自 [P] 并行

---

## Parallel Example: User Story 2 Tests

```bash
# 先并行写 US2 全部测试(确认 FAIL):
Task: "test_auth_service.py 登录/锁定测试"        # T020
Task: "test_auth_middleware.py 令牌/伪造tenant测试" # T021
Task: "test_deps.py get_current_user/require_tenant" # T022
Task: "test_account_flow.py 注册→登录→home 全链路"   # T023
```

---

## Implementation Strategy

### MVP First (US1 + US2)

本 feature 的 MVP = US1(注册建租户)+ US2(登录进后台),两者均 P1。完成 Phase 1-4 即可演示"注册→登录→进后台"核心闭环。

1. Phase 1 Setup → Phase 2 Foundational(关键地基)
2. Phase 3 US1 → 独立验证注册事务
3. Phase 4 US2 → 独立验证登录+鉴权
4. **STOP & VALIDATE**:MVP 可演示

### Incremental Delivery

MVP(US1+US2)→ 加 US3 登出失效 → 加 US4 越权拦截 → Polish 回归。每步不破坏前序。

---

## Notes

- Redis 是失败计数/黑名单的唯一数据源;DB 的 login_fail_count/locked_until 两列本 feature 不写(research R1)
- 中间件注册顺序逆序陷阱见 internal-interfaces,T029 落实
- 每完成一个任务或逻辑组即提交;每个 Checkpoint 可停下独立验证
