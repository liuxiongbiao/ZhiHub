# Implementation Plan: 账号体系 + 多租户闭环

**Branch**: `001-account-tenant` | **Date**: 2026-08-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-account-tenant/spec.md`

**开发模式**: SDD（规格驱动）+ TDD（测试先行）。本 feature 每个功能单元 MUST 先写失败测试 → 实现至通过 → 重构。

## Summary

打通「手机号注册 → 自动创建 trial 租户 → 登录拿 JWT → 进后台首页」全链路，并落地三条安全防线：登出令牌黑名单即时失效、连续失败锁定、接口级角色越权拦截。技术路径：FastAPI ASGI 中间件负责"是谁"（JWT 解析 + Redis 黑名单 + 上下文注入），`deps.py` 依赖负责"能不能做"（加载用户 / 角色白名单 / 租户状态），`auth_service` 编排注册事务与登录校验，CRUD 层只做数据存取。全程 TDD：先写单元/集成/端到端失败测试，再实现。

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 (ORM), Alembic, python-jose[cryptography] (JWT), passlib[bcrypt] (已由 Day 1 `core/security.py` 封装), redis (redis-py), pydantic v2

**Storage**: MySQL（users / tenants / tenant_members 主数据）+ Redis（令牌黑名单、登录失败计数，均带 TTL）

**Testing**: pytest + pytest-asyncio；FastAPI `TestClient`/`httpx.AsyncClient`；MySQL 用 SQLite in-memory 或事务回滚夹具隔离；Redis 用 `fakeredis` 或独立测试 DB

**Target Platform**: Linux 容器（docker compose），本地开发 Windows

**Project Type**: Web service（后端 FastAPI）+ 前端 Vue（前端本 plan 只覆盖 API 契约，页面归 🟢）

**Performance Goals**: 单请求鉴权开销（JWT 解码 + 一次 Redis 查询）p95 < 10ms；非本 feature 性能瓶颈

**Constraints**: 中间件 MUST 无业务逻辑；`tenant_id` MUST 仅来自令牌可信来源；鉴权 MUST 先验签再查黑名单

**Scale/Scope**: 本 feature 约 12 个源文件 + 4 层测试；后端多租户 SaaS，租户数与用户数按中小企业量级

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 本 feature 如何满足 | 判定 |
|---|---|---|
| I. 分层边界不可逾越 | 中间件仅解析令牌 + 查黑名单 + 注入 `request.state`（"是谁"）；`deps.py` 做角色/租户校验（"能不能做"）；`auth_service` 编排事务；`crud` 只存取；API 只粘合 | ✅ PASS |
| II. 多租户隔离红线 | `tenant_id` 只从 JWT claims 提取注入 `request.state.tenant_id`；客户端自报值一律忽略；越权用负向测试证明（US4） | ✅ PASS |
| III. 资金/状态防重防错 | 本 feature 不涉及资金/订单状态机；注册用 DB 事务保证"用户+租户+成员"原子性（无孤儿数据） | ✅ N/A（不涉及资金），事务原子性已覆盖 |
| IV. 端到端真实验收 | quickstart.md 定义端到端场景 + 强制异常分支（重复注册/弱密码/失败锁定/登出失效/越权/伪造 tenant_id）；🔴 文件逐行可讲 | ✅ PASS |
| V. 深度优先，不牺牲核心叙事 | 严格按 Day 2 裁剪（仅手机号+密码，验证码 mock）；不砍核心链路；mock 明确标注 | ✅ PASS |

**门禁结论**：无违背项，Complexity Tracking 无需填写。

## Project Structure

### Documentation (this feature)

```text
specs/001-account-tenant/
├── plan.md              # 本文件
├── spec.md              # 规格（已完成）
├── research.md          # Phase 0 — 技术决策
├── data-model.md        # Phase 1 — 实体 + Redis 键设计 + 状态
├── quickstart.md        # Phase 1 — TDD 运行 & 端到端验收指南
├── contracts/           # Phase 1 — API 契约 + 模块接口契约
│   ├── auth-api.md
│   └── internal-interfaces.md
└── checklists/
    └── requirements.md  # 规格质量清单（已完成）
```

### Source Code (repository root)

本 feature 触及的既有骨架文件（`zhihub/backend/app/` 下）：

```text
zhihub/backend/
├── app/
│   ├── core/
│   │   ├── security.py            # ✅ Day1 已完成（bcrypt + JWT 生成/解码），本 feature 复用
│   │   ├── redis.py               # 🔴 补：连接池 + 黑名单/失败计数辅助
│   │   ├── database.py            # 🆕 新建：engine + SessionLocal + get_db 生成器
│   │   └── exceptions.py          # 复用：统一鉴权/越权异常
│   ├── middlewares/
│   │   ├── auth_middleware.py     # 🔴 JWT 解析 + 黑名单查询 + 注入 current_user 身份
│   │   └── tenant_middleware.py   # 🔴 tenant_id 提取注入 request.state（隔离防线）
│   ├── api/
│   │   ├── deps.py                # 🔴 get_current_user / require_role / require_tenant
│   │   └── v1/
│   │       ├── auth.py            # 🟢 注册/登录/登出路由粘合
│   │       └── tenant.py          # 🟢 后台首页/租户信息路由粘合
│   ├── services/
│   │   └── auth_service.py        # 🟡 注册事务编排 + 登录校验 + 失败锁定 + 登出黑名单
│   ├── crud/
│   │   ├── user.py                # 🟢 按手机号查/建用户
│   │   └── tenant.py              # 🟢 建租户 + 建成员关联 + 查租户
│   ├── schemas/
│   │   ├── auth.py                # 🟢 注册/登录请求响应模型
│   │   └── tenant.py              # 🟢 租户/首页响应模型
│   ├── utils/
│   │   └── validators.py          # 🟢 手机号格式 + 密码强度规则
│   ├── models/                    # ✅ user/tenant/tenant_member 已完成
│   └── main.py                    # 🟡 挂载中间件（注意顺序）+ include auth/tenant 路由
└── tests/                         # 🆕 新建测试根（TDD 载体）
    ├── conftest.py                # 夹具：test app / db session / fake redis / token 工厂
    ├── unit/
    │   ├── test_security.py       # JWT 生成解码、bcrypt、随机 token
    │   └── test_validators.py     # 手机号格式、密码强度（弱密码表/连续字符/纯数字）
    ├── integration/
    │   ├── test_auth_service.py   # 注册事务回滚、登录失败锁定、登出黑名单写入
    │   ├── test_auth_middleware.py# 有效/过期/黑名单令牌、公开路径放行
    │   └── test_deps.py           # get_current_user / require_role 越权 / require_tenant 状态
    └── e2e/
        └── test_account_flow.py   # 注册→登录→进后台；登出后旧 token 失效；坐席越权 403
```

**Structure Decision**：沿用既有分层骨架（架构规约已定），本 feature 只填充 Day 2 相关文件 + 新建 `core/database.py`（DB 会话基础设施缺失）与 `tests/` 测试根。不引入新分层、不新增中间件类型。

## Complexity Tracking

> Constitution Check 无违背项，本节留空。
