# Implementation Plan: PRD 覆盖缺口闭环（前后端联调补齐）

**Branch**: `013-prd-coverage-gap-closure` | **Date**: 2026-08-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-prd-coverage-gap-closure/spec.md`

## Summary

审查报告确认后端 49 个端点基本全部实现，前端仅接通约 25 个。本 feature 以前端对接为主、后端补 3 处偏差为辅，采用 TDD（先写测试再实现）补齐 P0 级前后端联调缺口：多方式登录/注册短信验证、支付入口、坐席退回Agent+Trace、CSAT/留言建单、成员邀请/首页告警/快捷回复、订单发货、流式端点复用Agent。后端遵循 路由->service->crud 分层，前端遵循 api 封装->store->view 分层。

## Technical Context

**Language/Version**: 后端 Python 3.11 + FastAPI；前端 Vue 3.4 + TypeScript 5.4

**Primary Dependencies**: 后端 FastAPI/SQLAlchemy(ORM)/Alembic/Celery/LangGraph/Pydantic v2/litellm/pytest/fakeredis；前端 Vue3/Pinia/Axios/Element Plus/Vite/vitest/@vue/test-utils

**Storage**: MySQL(业务主库) + Redis(缓存/配额/黑名单/分布式锁) + MongoDB(trace) + Milvus(向量) + Elasticsearch(BM25)；测试用 SQLite in-memory + fakeredis

**Testing**: 后端 pytest（conftest: SQLite+StaticPool+fakeredis+TestClient+make_token）；前端 vitest + jsdom + @vue/test-utils（tests/setup.ts 补齐 ResizeObserver/matchMedia）

**Target Platform**: Linux 服务器（后端）+ 现代浏览器（前端 SPA）

**Project Type**: web-service（前后端分离）

**Performance Goals**: 演示级--单租户端到端链路可走通；坐席工作台新会话感知≤10s（现有轮询基线，WS 为后续优化）

**Constraints**: 宪法原则 I-V 不可违背；分层边界、租户隔离、资金/状态防重防错为红线；demo 环境短信/支付可 mock

**Scale/Scope**: 单租户演示；6 个 user story；前端新增/改造约 12 个文件，后端新增/修改约 6 个文件

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 检查 | 结论 |
|---|---|---|
| I. 分层边界不可逾越 | 新增 /order/{id}/ship 端点仅做参数校验+调 service；流式端点改调 agent_service 而非路由层直接 litellm；注册验证码校验放 service 层 | ✅ 通过 |
| II. 租户隔离红线 | 发货端点用 _get_tenant_id + order_crud 按 (tenant_id, order_id) 查询；CSAT/留言/快捷回复均带 tenant_id 过滤 | ✅ 通过 |
| III. 资金与状态变更防重防错 | 发货状态机校验（仅 PAID/PROCESSING->SHIPPED）+ 乐观锁 version；CSAT 去重（一会话一条，409） | ✅ 通过 |
| IV. 端到端验收以真实场景为准 | 每个 user story 有独立验收场景 + TDD 测试覆盖正常与异常分支（验证码错/重复支付/重复CSAT/非法发货） | ✅ 通过 |
| V. 深度优先 | 不动四条核心链路（LangGraph/混合检索/MCP/HITL），仅接线+补发货/流式偏差 | ✅ 通过 |

无违反项，无需 Complexity Tracking。

## Project Structure

### Documentation (this feature)

```text
specs/013-prd-coverage-gap-closure/
├── plan.md              # 本文件
├── research.md          # Phase 0：技术决策
├── data-model.md        # Phase 1：实体与状态机
├── quickstart.md        # Phase 1：端到端验收指南
├── contracts/           # Phase 1：API 契约
│   └── endpoints.md
└── tasks.md             # Phase 2：TDD 任务（/speckit-tasks 输出）
```

### Source Code (repository root)

```text
zhihub/
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   │   ├── order.py          # 新增 POST /order/{id}/ship
│   │   │   └── session.py        # 修改 /message/stream 复用 agent_service
│   │   ├── schemas/
│   │   │   ├── auth.py           # RegisterRequest 增 code 字段
│   │   │   └── order.py          # 新增 ShipRequest/ShipResponse
│   │   ├── services/
│   │   │   ├── auth_service.py   # register() 增验证码校验
│   │   │   └── order_service.py  # 新增 ship_order()
│   │   └── models/order.py       # VALID_TRANSITIONS 增 SHIPPED；tracking_no 字段
│   └── tests/
│       ├── contracts/test_order_ship.py     # TDD: 发货端点契约
│       ├── contracts/test_register_sms.py   # TDD: 注册验证码契约
│       ├── contracts/test_streaming_agent.py # TDD: 流式复用 Agent 契约
│       └── unit/test_order_state_machine.py # 扩展 SHIPPED 跃迁单测
└── frontend/
    ├── src/
    │   ├── api/
    │   │   ├── auth.ts           # 增 smsSend/loginSms/loginEmail/registerEmail/wechat系列
    │   │   ├── session.ts        # 增 getTrace/leaveMessage/submitCsat/getQuickReplies
    │   │   ├── billing.ts        # 增 payInvoice
    │   │   ├── tenant.ts         # 增 inviteMember/getAuditLogs/updateWidget
    │   │   └── order.ts          # 增 shipOrder/cancelRefund + Order/Refund 枚举
    │   ├── types/                # 增对应类型
    │   ├── views/
    │   │   ├── login/Login.vue           # 三选项卡 + 验证码登录 + 微信扫码
    │   │   ├── login/Register.vue        # 短信验证码步骤 + 邮箱注册
    │   │   ├── billing/InvoiceList.vue   # 支付按钮
    │   │   ├── session/AgentConsole.vue  # 退回AI + Trace + 快捷回复
    │   │   ├── session/ChatWindow.vue    # CSAT 评价 + 留言入口
    │   │   ├── dashboard/Home.vue        # 告警横幅
    │   │   └── order/OrderList.vue       # 发货操作
    │   └── components/            # 复用 TraceTimeline；新增 CsatDialog/LeaveMessageDialog
    └── tests/
        ├── unit/auth-api.spec.ts          # TDD: auth api 封装
        ├── unit/session-api.spec.ts       # TDD: session api 封装
        └── components/Login.spec.ts       # 扩展三选项卡测试
```

**Structure Decision**: 采用 Option 2 Web application 结构（backend + frontend 已存在）。本 feature 在既有结构内增改文件，不新增顶层目录。后端测试按 contracts/unit 分层，前端按 unit/components 分层，与既有约定一致。
