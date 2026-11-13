# 04 测试体系评估报告（Testing Assessment）

> 基线：`main` @ `7c6496b`
> 本报告中的测试结果与覆盖率均来自 **2026-11-13 在本机实际运行**，不是估算。
> 运行方式：`backend/.venv`（Python 3.11.9），按 CI 设置环境变量 `APP_ENV=test`、`APP_SECRET_KEY=ci-only-secret-key`，执行
> `python -m pytest tests -q -p no:cacheprovider --cov=app`。覆盖率数据与缓存写入系统临时目录，未在仓库中留下文件。
> 本机未运行 MySQL、Redis、MongoDB、Milvus、Elasticsearch、RabbitMQ。前端测试**未运行**。

---

## 1. 当前测试框架

| 端 | 框架与工具 | 配置 |
|---|---|---|
| 后端 | pytest 9.1.1、pytest-asyncio 1.4.0（`asyncio_mode = "auto"`）、pytest-cov 7.1.0、fakeredis 2.37.0、FastAPI `TestClient` | `backend/pyproject.toml:7-10`；夹具 `backend/tests/conftest.py` |
| 前端 | vitest 4（单元/组件，jsdom）、@vue/test-utils、Playwright（脚本式 E2E，`.cjs`） | `frontend/package.json` |
| 后端评测/验收脚本 | `scripts/agent_eval.py`、`ragas_eval.py`、`e2e_resolution_eval.py`、`benchmark_*.py`、`load_test.py`、`verify_*.py`、`real_mysql_smoke.py` | 手动执行，不在 CI 中 |

### 1.1 测试替身策略（`backend/tests/conftest.py`）

| 依赖 | 测试中替换为 | 覆盖范围 |
|---|---|---|
| MySQL | SQLite 内存库（`StaticPool`，每个测试建表/删表） | 只替换 `Depends(get_db)` 注入的 Session |
| Redis | fakeredis（每测试独立 `FakeServer`，autouse） | 替换 `core.redis._redis_client` 单例 |
| Mongo 运营埋点 | 内存假集合（autouse） | 只替换 `analytics_service._collection` |
| 支付宝 / 微信 / OCR | 强制 mock 模式（autouse） | — |
| Mongo trace、Milvus、ES、LLM、RabbitMQ | **未统一替换**，由各测试自行 monkeypatch | 未打桩的路径会尝试真实连接 |

---

## 2. 测试目录结构与数量

### 2.1 后端

| 目录 | 文件数 | `def test_` 数 | 类型 | CI 是否运行 |
|---|---|---|---|---|
| `tests/unit` | 19 | 170 | 单元测试（纯函数、节点、客户端、状态机） | ✅ |
| `tests/contracts` | 55 | 595 | 契约测试：经 `TestClient` 走真实中间件链，验证 HTTP 状态码与响应结构 | ✅ |
| `tests/integration` | 11 | 67 | 集成测试：认证中间件、知识库任务、并发重置、MCP 安全、可观测、会话生命周期、Alembic 从零建库 | ❌ |
| `tests/e2e` | 1 | 8 | 账户流程端到端（`TestClient`） | ❌ |
| **合计** | 86 | 840 | 参数化展开后 pytest 收集 **860** 个用例 | — |

测试代码 12,416 行，业务代码 21,445 行，测试/业务行数比约 0.58。

### 2.2 前端

| 目录 | 文件数 | 类型 | CI 是否运行 |
|---|---|---|---|
| `frontend/tests/unit` | 20 | API 模块、store、路由、权限、WebSocket 客户端、`widget.js` | ✅（`vitest run`） |
| `frontend/tests/components` | 15 | 组件测试 | ✅ |
| `frontend/tests/integration` | 1 | 认证流程 | ✅ |
| `frontend/tests/e2e` | 17 个 `.cjs` + 1 个 `.py` | Playwright 脚本，需要真实运行的前后端 | ❌ |

---

## 3. 实际运行结果

### 3.1 后端全量测试

```
2 failed, 856 passed, 2 skipped, 398 warnings in 1432.75s (0:23:52)
```

**失败用例（均由 `01_architecture_audit.md` P1-06 的路由回归导致）**：

| 用例 | 错误 |
|---|---|
| `tests/contracts/test_session_handoff_batch3.py::TestAgentMessageHandoff::test_user_message_during_handoff_is_rejected_before_agent_execution` | `fastapi.exceptions.ResponseValidationError` |
| `tests/contracts/test_session_lifecycle.py::TestMessageRateLimit::test_degraded_tenant_returns_200_not_500` | `fastapi.exceptions.ResponseValidationError` |

这两个用例位于 `tests/contracts`，属于 CI 运行范围。也就是说，如果 CI 在 `7c6496b` 上正常运行，应当会失败；该提交仍在 `main` 上，说明 CI 结果没有阻断合并，或者 CI 没有运行（本机无 `gh`，未能核实）。

`tests/integration/test_session_lifecycle_e2e.py:154-163` 也调用了同一个接口但通过了：它验证的是限流返回 429 的路径，而被错误注册的 `_reject_busy_user` 恰好仍执行限流检查。这也说明现有用例只覆盖了该接口的部分路径。

**跳过用例**：`tests/integration/test_alembic_from_scratch.py:72,116`（需要 `MYSQL_ROOT_PASSWORD` 建临时库），共 2 个。

**修复后复测（2026-11-13，分支 `fix/session-message-route-7c6496b`）**：

```
863 passed, 2 skipped, 404 warnings in 1454.02s (0:24:14)
```

用例数从 860 增加到 865（新增回归测试 5 个），2 个失败用例转为通过，跳过的 2 个不变；语句覆盖率 78.9%（10,270 条语句，未覆盖 2,171 条）。详见 `05_fix_report_session_message.md`。

**耗时问题**：23 分 52 秒中大部分时间在等待网络超时。运行期间观察到测试进程持续向 `127.0.0.1:3307`（MySQL）发起连接，并输出大量 `Failed to save trace: localhost:27017` 错误。原因见第 5.1 节。

### 3.2 覆盖率基线

| 指标 | 值 |
|---|---|
| 统计范围 | `backend/app`（`--cov=app`） |
| 语句数 | 10,270 |
| 未覆盖语句 | 2,190 |
| **语句覆盖率** | **78.7%** |
| 分支覆盖率 | **未统计**（未启用 `--cov-branch`） |

> 说明：该数字包含 `tests/integration` 与 `tests/e2e`。CI 只运行 `unit + contracts`，CI 中的覆盖率会更低，本次未单独测量。

**关键模块覆盖率**：

| 模块 | 语句数 | 覆盖率 | 说明 |
|---|---|---|---|
| `core/crypto.py` | 39 | 100.0% | PII 加密 |
| `tasks/billing_tasks.py` | 83 | 98.8% | 覆盖率很高，但 P1-01 的月末漏计问题**没有被测试发现**——覆盖率不等于业务正确性 |
| `services/auth_service.py` | 489 | 86.3% | — |
| `services/human_service.py` | 87 | 85.1% | — |
| `services/mcp_service.py` | 137 | 84.7% | — |
| `tasks/knowledge_tasks.py` | 231 | 84.4% | — |
| `services/tenant_service.py` | 408 | 84.3% | — |
| `services/order_service.py` | 349 | 84.0% | 退款资金链路 |
| `services/session_service.py` | 231 | 83.1% | — |
| `core/ws_manager.py` | 161 | 82.6% | — |
| `services/billing_service.py` | 341 | 78.6% | — |
| `services/agent_service.py` | 395 | 70.6% | 对话主链路 |
| `services/knowledge_service.py` | 126 | 69.8% | 试点候选 |
| `api/v1/session.py` | 195 | 64.6% | — |
| `services/retrieval_service.py` | 276 | 64.1% | 试点候选 |
| `tasks/cleanup_tasks.py` | 384 | 62.5% | 含租户物理删除 |
| `api/v1/billing.py` | 53 | 60.4% | 含支付回调 |
| `services/trace_service.py` | 115 | 53.0% | — |
| `core/alipay_client.py` | 87 | **44.8%** | 真实模式（验签、退款、退款查询）基本未覆盖 |

**覆盖率最低的模块（≥40 语句）**：`core/ragflow_client.py` 21.7%、`tasks/deep_agent_tasks.py` 25.6%、`api/v1/agent.py` 31.6%、`core/deep_agent_store.py` 31.9%、`core/rabbitmq.py` 35.7%、`api/v1/demo.py` 38.1%、`core/alipay_client.py` 44.8%、`core/langfuse_client.py` 48.9%、`core/llm_client.py` 49.0%。

---

## 4. 关键业务的测试保护情况

| 关键业务 | 已有测试（文件） | 保护程度 | 缺口 |
|---|---|---|---|
| 退款申请 / 分级审批 / 撤销 / Outbox | `test_refund_batch1.py`、`test_refund_closure.py`、`test_refund_outbox.py`、`test_refund_event_db_immutability.py`、`unit/test_order_state_machine.py` | 较好 | 不可变触发器只在 MySQL 生效，SQLite 测试只能验证 ORM 层；真实支付宝退款/查询路径未覆盖 |
| 账单与计费 | `test_billing_accuracy.py`、`test_billing_batch2.py`、`test_billing_tasks.py`、`test_billing_plan.py`、`test_billing_export.py` | 中 | **跨月边界**（月末最后一天 02:00 之后的用量）无测试；自定义配额对账单的影响无测试；导出口径与计费口径一致性无测试 |
| 支付回调 | `test_payment.py` | 中 | 只在 mock 验签下测试；金额/`app_id` 校验不存在故无测试 |
| 认证与令牌 | `test_register_sms.py`、`test_refresh_token_batch5.py`、`test_wechat_login.py`、`test_account_ext.py`、`integration/test_auth_*` | 较好 | — |
| 租户隔离 | 散落在各契约测试中 | 中 | 没有系统化的“每个端点 × 跨租户访问”矩阵测试 |
| 租户状态守卫 | `test_tenant_cancellation.py`、`test_tenant_wizard.py` | 低 | 订单/工单/知识库/MCP 在 `pending_deletion`、`readonly` 下的行为无测试（P1-05） |
| 租户物理删除 | `test_tenant_cancellation.py`、`scripts/verify_tenant_cancellation.py` | 中 | Mongo/Milvus/ES 删除依赖打桩；没有“新增表必须进入删除清单”的守护测试 |
| 对话主链路与流式 | `test_streaming_agent.py`、`test_business_streaming.py`、`test_session_lifecycle.py`、`unit/test_graph.py` | 中 | 客户端中途断开、同一会话并发消息无测试 |
| 转人工与坐席 | `test_session_handoff_batch3.py`、`unit/test_human_handoff.py`、`test_websocket.py` | 中 | 跨进程广播（Worker → API → 浏览器）无测试（P2-17 正是因此未被发现） |
| 知识库与检索 | `integration/test_knowledge_*`、`unit/test_retrieval_service.py`、`unit/test_file_parser.py` | 中 | 检索效果只有离线评测脚本，无回归门禁 |
| MCP 权限 | `test_mcp_protocol.py`、`unit/test_mcp_permission.py`、`integration/test_mcp_security_e2e.py` | 较好 | — |
| 数据库迁移 | CI 中 `alembic upgrade head` + `alembic check` | 中 | `downgrade` 未测试；`test_alembic_from_scratch` 本机被跳过 |

---

## 5. 测试与生产行为不一致的问题

### 5.1 部分代码路径绕过测试替身

`conftest.py` 只覆盖了 `Depends(get_db)`。下列代码自行创建 `SessionLocal()`，在测试中会连接配置中的真实 MySQL（默认 `localhost:3307`）：

| 位置 | 场景 |
|---|---|
| `app/mcp_servers/order_server.py:86`、`ticket_server.py:138` | 智能体调用工具 |
| `app/services/agent_service.py:170,216` | 转人工估时、转人工通知 |
| `app/middlewares/widget_cors_middleware.py:53` | Widget CORS 白名单加载 |
| `app/api/v1/ws.py:186` | 终端用户 WebSocket 校验 |
| `app/agents/deep_agent/planner.py:376` | DeepAgent |

同样，`trace_service` 每次新建 Mongo 客户端，测试中会连接 `localhost:27017`。

后果：测试结果取决于开发机是否碰巧开着这些服务；没有开时，相关逻辑走异常分支（多数被 `except Exception` 吞掉），测试“通过”但并没有验证到真实逻辑，同时整体运行时间被超时拉长到 20 分钟以上。

### 5.2 SQLite 与 MySQL 的差异

| 行为 | MySQL（生产） | SQLite（测试） | 受影响功能 |
|---|---|---|---|
| 触发器 | `refund_approval_events` 禁止 UPDATE/DELETE | 不创建 | 退款审计不可变性 |
| `SELECT ... FOR UPDATE` | 行锁 | 忽略 | 租户删除、并发控制 |
| 隔离级别 | `REPEATABLE READ` | 串行化写 | 所有并发测试 |
| `DATE LIKE 'YYYY-MM-%'` | 隐式转换为字符串 | 按存储文本匹配 | 账单用量汇总 |
| `JSON` 列 | 原生 JSON | TEXT | `messages.sources/metadata` |
| 字符集与排序规则 | `utf8mb4`，默认大小写不敏感比较 | 大小写敏感 | 唯一约束、查重 |
| `NOW()` 时区 | 服务器时区 | UTC | 所有 `created_at` 比较 |
| 字段长度 | 超长报错或截断（取决于 SQL 模式） | 不检查 | 所有 `String(n)` 字段 |

CI 中已经有 MySQL 服务（用于 `alembic check`），但测试本身没有在 MySQL 上运行。

### 5.3 其他差异

- 本机 Python 3.11.9，镜像与 CI 为 3.12。
- 测试强制支付宝/微信/OCR 走 mock，真实模式代码（验签、退款查询）覆盖率低（`alipay_client.py` 44.8%）。
- 按 README 直接执行 `pytest tests/ -v` 会在导入阶段失败（需要 `APP_SECRET_KEY`）。

---

## 6. 服务间契约测试

| 契约 | 是否有自动化测试 |
|---|---|
| 后端 HTTP API 的状态码与响应结构 | ✅ `tests/contracts`（595 个），但不是快照式，新增/删除路由不会被自动发现 |
| OpenAPI 路由与 Schema 快照 | ❌ 无（P1-06 这类“路由丢失”只能靠恰好存在的用例发现） |
| 前端 ↔ 后端 | ❌ 前端测试使用 mock 响应，无共享契约 |
| `widget.js` ↔ 后端（对第三方站点的公开契约） | 🔶 前端 `widget-js.spec.ts` 与后端 `test_widget_*` 各自测试，未互相约束 |
| WebSocket 消息信封 | 🔶 `test_widget_p02.py`、`ws-envelope-batch3.spec.ts` 各自测试 |
| Celery 任务参数 | ❌ 无 |
| 支付宝回调格式 | 🔶 mock 下测试 |

---

## 7. 数据库事务与消息处理的测试

| 主题 | 现状 |
|---|---|
| 乐观锁 / 条件更新 | 有单元与契约测试，但在 SQLite 上运行，无法验证真实并发 |
| 并发场景 | `integration/test_knowledge_reset_concurrency.py`（SQLite 上的线程并发），不在 CI 中 |
| 事务回滚 | 部分契约测试覆盖异常路径 |
| Celery 任务 | 直接调用任务函数测试业务逻辑（`test_billing_tasks.py`、`integration/test_knowledge_tasks.py`），**不测试**投递、路由、重试、`acks` 语义 |
| 跨进程广播 | 测试中直接 mock 广播函数，不验证投递（P2-17） |

---

## 8. 建议的测试体系

### 8.1 分层目标

| 层 | 目标 | 运行环境 | CI 阶段 |
|---|---|---|---|
| 单元 | 纯业务规则（状态机、计费公式、权限矩阵、校验），不碰 IO | 无依赖 | 每次提交，< 1 分钟 |
| 契约（API） | 每个路由的状态码、响应 Schema、鉴权、租户隔离 | SQLite + fakeredis，**所有外部依赖统一打桩** | 每次提交，目标 < 5 分钟 |
| OpenAPI 快照 | 路由清单与 Schema 不发生意外变化 | 无依赖 | 每次提交 |
| 集成（MySQL） | 触发器、行锁、并发 CAS、迁移、计费跨月、租户删除 | CI 中已有的 MySQL + 真实 Redis 服务 | 每次 PR |
| 端到端 | 关键用户旅程（注册 → 上传文档 → 对话 → 转人工 → 退款 → 账单） | Compose 全栈 | 合并到 `main` 后 / 发布前 |
| 效果评测 | 检索与智能体质量（已有 `ragas_eval.py`、`agent_eval.py`） | 真实模型 | 涉及知识/智能体模块的 PR，手动或夜间 |

### 8.2 具体改进项（按优先级）

1. **修复测试隔离**（P1-08）：为 `SessionLocal` 提供可替换的工厂，conftest 统一替换；统一打桩 Mongo trace、Milvus、ES、LLM。目标是本机无任何容器时，全量测试 5 分钟内完成且不发起任何外部连接（可在 conftest 中用 socket 守卫断言）。
2. **OpenAPI 快照测试**：把 `app.openapi()` 的路由、方法、请求/响应 Schema 存为基线文件，变化必须显式更新基线。
3. **表征测试（重构前必须完成）**：对计费跨月、配额口径、租户状态守卫、退款额度为 0、导出口径、流式断开等“待业务确认”的行为，先按**当前行为**写测试并标注“待确认”，业务决定后再修改预期。
4. **MySQL 集成测试进 CI**：复用 CI 中的 MySQL 服务，新增 `tests/mysql/`，覆盖触发器、`FOR UPDATE`、并发 CAS、`usage_records` 汇总；同时打开 `test_alembic_from_scratch`。
5. **覆盖率门禁**：开启 `--cov-branch`，以本次基线为下限设置“不下降”规则；对资金模块（`order_service`、`billing_service`、`billing_tasks`、`alipay_client`）单独设更高阈值。
6. **跨租户访问矩阵测试**：遍历所有需要鉴权的路由，用另一个租户的令牌访问，断言 403/404。
7. **数据删除守护测试**：遍历 `Base.metadata.tables`，断言每张表都在租户删除流程中被处理。
8. **前后端契约**：从后端 OpenAPI 生成前端类型，或在前端测试中校验 mock 响应符合 OpenAPI Schema。
9. **静态检查**：引入 ruff（先告警后阻断）、`import-linter`（模块依赖方向）。
10. **统一 Python 版本**：本地、CI、镜像统一 3.12。

### 8.3 每个重构阶段的测试与回滚要求

| 变更类型 | 合并前必须通过 | 回滚方式 |
|---|---|---|
| 纯结构移动（拆文件、改导入） | 单元 + 契约 + OpenAPI 快照无差异 | `git revert` |
| 横切能力（日志、错误格式） | 同上 + 错误响应向后兼容测试 | `git revert` |
| 运行时行为（异步、线程、连接） | 同上 + 压测对比（延迟、连接数、线程数） | 功能开关 / `git revert` |
| 数据库结构 | 同上 + MySQL 集成 + `alembic upgrade/downgrade` 往返 + 预发环境演练 | 只做扩展式迁移，回滚时保留新列 |
| 业务规则（需业务确认） | 同上 + 更新后的表征测试 + 业务签字记录 + 财务对账演练（资金类） | 功能开关，默认关闭 |

---

## 9. 结论

- 测试**数量充足**（后端 860 个用例，语句覆盖率 78.7%），资金和认证模块有成体系的契约测试，这是重构的良好起点。
- 但测试**不够可信**：部分路径在测试中连接真实服务、MySQL 特有行为不被测试、CI 不运行集成测试、没有路由快照，且最近的回归已经进入 `main`。
- 覆盖率高的模块（如 `billing_tasks.py` 98.8%）仍存在业务口径问题，说明需要基于业务场景的表征测试，而不只是追求行覆盖。
- 在开始任何重构之前，应先完成第 8.2 节的第 1~5 项。
