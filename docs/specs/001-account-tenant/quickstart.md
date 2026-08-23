# Quickstart: 账号体系 + 多租户闭环（TDD 运行 & 端到端验收指南）

本文件是本 feature 的**验收运行手册**，同时定义 TDD 的红绿循环起点。实现细节见 tasks.md，不在此重复代码。

## 前置

```bash
cd zhihub/backend
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest pytest-asyncio fakeredis              # 测试依赖
```

测试用 SQLite in-memory + fakeredis，**无需启动 MySQL/Redis 容器**即可跑全部单元/集成/e2e 测试。真实 docker 环境仅在手动端到端演示时需要。

## TDD 工作流（每个单元遵循）

1. **RED**：先写测试，运行 `pytest`，确认因功能未实现而失败。
2. **GREEN**：写最小实现让测试通过。
3. **REFACTOR**：在测试保护下重构，保持绿灯。

严格顺序：`tests/` 先于对应源文件提交。

## 分层测试与运行命令

```bash
# 全部
pytest

# 分层（TDD 推进顺序：unit → integration → e2e）
pytest tests/unit                    # security / validators 纯函数
pytest tests/integration             # auth_service / 中间件 / deps
pytest tests/e2e                     # 注册→登录→进后台全链路
pytest -k "lockout or blocklist or forbidden"   # 关键安全负向用例
```

## 验收场景（对应 spec 的 Success Criteria）

### SC-001 注册建租户事务（US1）
```
POST /api/v1/auth/register {phone, password, tenant_name}
→ 201，DB 新增 1 user + 1 trial tenant + 1 admin member
异常：注册中途失败 → 三表均无残留（事务回滚）
```
测试：`tests/integration/test_auth_service.py::test_register_transaction_rollback`

### SC-002 登录进后台（US2）
```
POST /api/v1/auth/login → 200 + access_token
GET  /api/v1/tenant/home (Bearer) → 200，tenant_id 与注册一致
```
测试：`tests/e2e/test_account_flow.py::test_register_login_home`

### SC-003 失败锁定（US2）
```
连续 5 次错误密码 → 第 5 次后 423
锁定期内正确密码 → 仍 423，提示剩余秒数
```
测试：`tests/integration/test_auth_service.py::test_login_lockout_after_5_failures`

### SC-004 登出即时失效（US3）
```
登录拿 token → POST /auth/logout → 200
用同一 token → GET /tenant/home → 401（黑名单命中）
```
测试：`tests/e2e/test_account_flow.py::test_logout_blocklists_token`

### SC-005 越权拦截（US4）
```
agent 令牌 → GET /api/v1/tenant/billing → 403
admin 令牌 → GET /api/v1/tenant/billing → 200
```
测试：`tests/integration/test_deps.py::test_require_role_forbids_agent`

### SC-006 伪造 tenant_id 无效（US4）
```
tenant=1 的令牌 → GET /tenant/home?tenant_id=999
→ 返回 tenant_id 恒为 1（客户端自报值被忽略）
```
测试：`tests/integration/test_auth_middleware.py::test_client_tenant_id_ignored`

## 手动端到端演示（真实容器）

```bash
cd zhihub/deploy && docker compose up -d      # 起 MySQL + Redis
cd ../backend && alembic upgrade head          # 建表
uvicorn app.main:app --reload
# 然后按 contracts/auth-api.md 用 curl/Postman 走 注册→登录→home→logout→越权 六步
```

## 完成定义（Definition of Done）

- [ ] 四层测试全绿（unit/integration/e2e），关键负向用例覆盖锁定/黑名单/越权/伪造 tenant_id
- [ ] `pytest` 一条命令通过
- [ ] 三个 🔴 文件（auth_middleware / tenant_middleware / deps）实现者能逐行讲解
- [ ] 手动端到端六步演示无报错
