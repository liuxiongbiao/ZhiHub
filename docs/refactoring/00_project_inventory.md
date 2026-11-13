# 00 全项目资产清单（Project Inventory）

> 阶段：第一阶段 · 只读扫描与架构审计
> 扫描日期：2026-11-13
> 扫描基线：`main` 分支，HEAD = `7c6496b`（与 `origin/main` 一致，工作区干净）
> 约束：本阶段未修改任何业务代码、配置、数据库、Docker 或部署文件；仅新增 `docs/refactoring/` 下五份文档。

---

## 0. 项目形态（以实际代码为准，已经项目负责人确认）

| 项 | 实际情况 | 证据 |
|---|---|---|
| Python 代码库 | **1 个**（`backend/`），用同一个镜像启动 5 个不同角色的容器 | `deploy/docker-compose.yml` 中 `migration`、`backend`、`celery-worker`、`celery-knowledge-worker`、`celery-beat` 均为 `build: ../backend` |
| Web 框架 | **只有 FastAPI**（`fastapi==0.141.1`）。项目中没有 Django 服务、代码或依赖 | `backend/requirements.txt:4`、`backend/app/main.py` |
| 关系数据库 | **只有 MySQL 8.0**。项目没有使用 PostgreSQL | `backend/app/core/database.py`（`mysql+pymysql`）；`backend/requirements.txt` 只有 `pymysql` 驱动 |
| 其他存储 | Redis、MongoDB、Milvus、Elasticsearch、本地文件卷 | 见第 6 节 |
| 前端 | Vue 3 SPA + 可嵌入第三方站点的 `widget.js` | `frontend/` |
| Git 仓库 | **1 个** | 见第 1 节 |

因此本审计把系统当作“**一个模块化单体（Modular Monolith），以多进程/多容器方式部署**”来分析。后续文档中的“服务”一词，指部署单元时写“容器/进程”，指代码内的业务模块时写“模块”。

> 说明：`deploy/docker-compose.yml` 的可选 profile `observability` 中，第三方组件 Langfuse 自带一个 `postgres:15-alpine` 容器作为其内部存储。它默认不启动，本项目代码不连接它，也不属于本项目的数据库。

---

## 1. Git 仓库

### 1.1 已扫描

| 仓库 | 本地路径 | 远端 | 分支 | 跟踪文件数 | 扫描深度 |
|---|---|---|---|---|---|
| ZhiHub | `C:\Users\Lenovo\Desktop\ZhiHub` | `https://github.com/liuxiongbiao/ZhiHub.git` | `main`（仅此一个分支） | 567（不含 `node_modules`） | 后端：全部目录结构 + 关键业务代码精读；前端：结构与 API 调用层；部署/CI：全部 |

### 1.2 未扫描 / 无法访问（如实标注）

| 对象 | 原因 |
|---|---|
| 桌面上的其他目录（`company`、`ZRN` 等） | 不在当前工作区内，未读取。项目负责人已确认本项目没有其他服务 |
| GitHub 上的 CI 运行记录 | 本机未安装 `gh` CLI，无法查询 `7c6496b` 的 CI 结果 |
| 任何生产环境（服务器、生产库、生产 Redis） | 本阶段只读扫描代码仓库，未连接任何运行环境 |
| 本地 `.env` 文件 | `backend/.env`、`deploy/.env` 当前不存在于工作区；`git log` 显示从未被提交。按审查策略未做密钥内容扫描 |
| `frontend/node_modules` | 第三方依赖，未扫描 |

---

## 2. 部署单元（容器）清单

来源：`deploy/docker-compose.yml`（单机 Docker Compose，未发现 K8s / Helm / Swarm 配置）。

### 2.1 自研代码容器（全部来自 `backend/` 同一镜像，前端除外）

| 容器 | 启动命令 | 职责 | 依赖 |
|---|---|---|---|
| `migration` | `python -m alembic upgrade head` | 一次性数据库迁移，成功后其他容器才启动 | mysql |
| `backend` | `uvicorn app.main:app --workers 2 --proxy-headers` | HTTP API、SSE 流式、WebSocket、MCP HTTP 入口 | mysql, redis, rabbitmq, mongo, migration |
| `celery-worker` | `celery worker -Q maintenance,celery --concurrency=1` | 周期维护、计费聚合、账单生成、DeepAgent 任务、通知 | redis, rabbitmq, mysql |
| `celery-knowledge-worker` | `celery worker -Q knowledge,knowledge.0..7 --concurrency=2 -Ofair` | 知识库文档解析、切片、向量化、入库 | redis, rabbitmq, mysql, upload_data 卷 |
| `celery-beat` | `celery beat` | 定时调度（每日 02:00 UTC 聚合、每月 1 日 03:00 UTC 账单、每 5 分钟维护） | rabbitmq |
| `frontend` | `frontend/Dockerfile`（Nginx 托管 SPA 构建产物） | Vue 3 管理后台 + `public/widget.js` | — |
| `nginx` | `nginx:alpine` + `deploy/nginx.conf` | 反向代理：`/api/` → backend，`/api/v1/ws/` WebSocket 升级，`/` → frontend | backend, frontend |

### 2.2 基础设施容器

| 容器 | 镜像 | 用途 | 数据卷 | 备注 |
|---|---|---|---|---|
| mysql | `mysql:8.0` | 业务主库 | `mysql_data` | `--log-bin-trust-function-creators=1`（为退款事件不可变触发器） |
| redis | `redis:7-alpine` | 缓存、配额计数、幂等键、锁、上下文、黑名单、Pub/Sub | **无** | `mem_limit: 256m`，未配置 `maxmemory-policy` |
| mongo | `mongo:7.0` | Agent trace、DeepAgent 任务、运营埋点 | `mongo_data` | 默认无认证（生产校验要求带认证） |
| rabbitmq | `rabbitmq:3.12-management` | Celery broker | **无** | 队列持久化依赖容器生命周期 |
| elasticsearch | `elasticsearch:8.13.0` | 知识切片 BM25 检索 | `es_data` | `xpack.security.enabled=false` |
| milvus + etcd + minio | `milvusdb/milvus:v2.4.0` 等 | 知识切片向量、用户长期记忆向量 | 各自卷 | — |
| langfuse（及其自带的 langfuse-db、clickhouse） | profile `observability` | LLM 调用可观测性（第三方组件） | 各自卷 | 默认不启动；其内部存储不属于本项目数据库 |

所有端口都绑定在 `127.0.0.1`，只有 nginx 对外暴露 80 端口（未配置 HTTPS，需人工确认生产是否有外层 TLS 终结）。

---

## 3. 技术栈与版本

### 3.1 Python 运行时

| 位置 | 版本 |
|---|---|
| `backend/Dockerfile` | `python:3.12-slim`（多阶段构建） |
| `.github/workflows/ci.yml` | `python-version: "3.12"` |
| `backend/pyproject.toml` | `requires-python = ">=3.11"` |
| 本机 `backend/.venv`（本次运行测试所用） | **Python 3.11.9** ← 与镜像/CI 不一致 |

### 3.2 主要依赖（`backend/requirements.txt`，全部 `==` 锁定）

| 类别 | 依赖 |
|---|---|
| Web 框架 | `fastapi==0.141.1`、`starlette==1.3.1`、`uvicorn[standard]==0.52.1`、`pydantic==2.13.4`、`pydantic-settings==2.14.2`、`python-multipart==0.0.32` |
| ORM / 迁移 / 驱动 | `sqlalchemy==2.0.51`（**同步**模式）、`alembic==1.19.0`、`pymysql==1.2.0` |
| 其他存储客户端 | `redis==8.1.0`、`motor==3.7.1`（Mongo 异步）、`pymilvus==3.0.1`、`elasticsearch==8.19.3` |
| 消息 / 任务 | `celery==5.6.3`、`pika==1.4.2`（仅旧兼容入口） |
| AI / LLM | `langgraph==1.2.10`、`langchain==1.3.14`、`langchain-openai==1.6.0`、`litellm==1.103.1`、`tiktoken==0.13.0`、`langfuse==4.14.2` |
| 安全 | `PyJWT==2.15.1`、`cryptography==50.0.0`、`bcrypt==5.0.0` |
| 外部集成 | `python-alipay-sdk==3.4.0`、`httpx==0.28.1` |
| 文件解析 | `pypdf==6.21.4`、`python-docx==1.2.0` |
| 监控 | `prometheus-client==0.26.0` |

**隐式依赖（代码直接 import，但 `requirements.txt` 未直接声明）**：

- `pymongo`：`backend/app/tasks/cleanup_tasks.py:237`、`backend/app/services/analytics_service.py:65`、`backend/app/core/deep_agent_store.py:23`（目前随 `motor` 传递安装）
- `requests`：`backend/app/core/ocr_client.py:100,125`（目前随其他依赖传递安装）

开发依赖（`requirements-dev.txt`）：`pytest==9.1.1`、`pytest-asyncio==1.4.0`、`pytest-cov==7.1.0`、`fakeredis==2.37.0`、`pip-audit==2.10.1`。**没有** lint / 格式化 / 类型检查工具（未发现 ruff、flake8、black、mypy、pyright 配置）。

### 3.3 前端

| 项 | 值 |
|---|---|
| 框架 | Vue 3（`^3.5.44`）+ TypeScript + Vite 6 + Element Plus + Pinia + vue-router 4 + axios |
| 测试 | vitest 4（单元/组件）、Playwright（`tests/e2e/*.cjs` 脚本式 E2E） |
| Node | CI 使用 20（`.nvmrc` 存在） |
| 源码文件 | `frontend/src` 下 70 个跟踪文件 |

---

## 4. 后端代码规模

| 目录 | Python 行数 | 说明 |
|---|---|---|
| `backend/app` | 21,445 | 业务代码 |
| `backend/tests` | 12,416 | 测试代码 |
| `backend/scripts` | 2,762 | 运维/验收/评测脚本 |
| `backend/alembic` | 1,488 | 38 个迁移文件 |

最大的 10 个业务文件：

| 行数 | 文件 |
|---|---|
| 1024 | `app/services/agent_service.py` |
| 1014 | `app/services/auth_service.py` |
| 863 | `app/services/tenant_service.py` |
| 855 | `app/demo/demo_seed.py` |
| 809 | `app/tasks/cleanup_tasks.py` |
| 744 | `app/services/order_service.py` |
| 647 | `app/services/retrieval_service.py` |
| 631 | `app/services/billing_service.py` |
| 560 | `app/api/v1/tenant.py` |
| 519 | `app/services/analytics_service.py` |

---

## 5. 后端模块与业务职责

代码分层（`backend/app/`）：`api/v1`（路由）→ `services`（业务编排、事务边界）→ `crud`（数据访问，不 commit）→ `models`（ORM）；横切的 `core`（客户端/基础设施）、`middlewares`、`agents`（LangGraph）、`mcp_servers`（工具协议适配）、`tasks`（Celery）。

| 业务模块 | 路由前缀 | 主要文件 | 职责 |
|---|---|---|---|
| 认证与账户 | `/api/v1/auth`（21 个端点） | `api/v1/auth.py`、`services/auth_service.py`、`core/security.py`、`core/redis.py` | 手机/邮箱/微信登录注册、短信验证码、JWT + Refresh Token 家族轮换、登出黑名单、登录锁定 |
| 租户 | `/api/v1/tenant`（23 个端点） | `api/v1/tenant.py`、`services/tenant_service.py`、`services/widget_service.py` | 配置向导、成员邀请、套餐升级、企业版审批、Widget 配置、注销/撤销/导出 |
| 会话与对话 | `/api/v1/session`（15 个端点）、`/api/v1/ws` | `services/session_service.py`、`services/agent_service.py`、`api/v1/ws.py`、`core/ws_manager.py` | 会话生命周期、发消息（同步/SSE 流式）、转人工、坐席认领/回复/退回、CSAT、反馈 |
| 智能体编排 | （内部） | `agents/graph.py`、`agents/nodes/*`、`agents/deep_agent/planner.py` | LangGraph 状态图：router → knowledge / ticket / order / human |
| 知识库与检索 | `/api/v1/knowledge` | `services/knowledge_service.py`、`services/retrieval_service.py`、`tasks/knowledge_tasks.py`、`core/embedding_client.py` | 文档上传/版本/有效期、异步解析切片、Milvus + ES 混合检索、RRF、Rerank |
| 工单 | `/api/v1/ticket` | `services/ticket_service.py`、`mcp_servers/ticket_server.py` | 工单创建/查询/更新 |
| 订单与退款 | `/api/v1/order`（9 个端点） | `services/order_service.py`、`mcp_servers/order_server.py`、`core/alipay_client.py` | 订单状态机、发货、退款申请/分级审批/撤销、退款 Outbox 与渠道对账 |
| MCP 工具 | `/api/v1/mcp` | `services/mcp_service.py`、`mcp_servers/*` | JSON-RPC 2.0 信封、角色-工具权限矩阵、熔断 |
| 计费与支付 | `/api/v1/billing` | `services/billing_service.py`、`tasks/billing_tasks.py` | Redis 配额计数、预警、每日用量落库、月度账单、支付宝支付与回调、逾期降级 |
| Widget 公共通道 | `/api/v1/widget` | `api/v1/widget.py`、`services/widget_service.py`、`middlewares/widget_cors_middleware.py` | 匿名会话（API Key + 域名白名单）、Widget 令牌、发消息、撤销退款 |
| 运营分析 | `/api/v1/analytics` | `services/analytics_service.py` | 埋点写 Mongo、看板 |
| 通知 | `/api/v1/notifications` | `services/notification_service.py`、`tasks/notification_tasks.py` | 站内信 |
| 演示 | `/api/v1/demo` | `api/v1/demo.py`、`demo/demo_seed.py`、`services/demo_story_service.py` | 演示数据与快速登录（生产强制关闭） |
| DeepAgent | `/api/v1/agent` | `api/v1/agent.py`、`tasks/deep_agent_tasks.py`、`core/deep_agent_store.py` | 长任务规划执行，结果写 Mongo |
| 运维 | `/health`、`/metrics` | `app/main.py` | 健康检查、Prometheus 指标（平台令牌保护） |

---

## 6. 数据存储清单（详见 `03_database_inventory.md`）

| 存储 | 用途 | 访问方式 |
|---|---|---|
| MySQL 8.0 | 21 张业务表 + `alembic_version`（权威数据） | SQLAlchemy 2.0 同步 ORM + pymysql；Alembic 迁移 |
| Redis 7 | 配额计数、幂等、锁、上下文、黑名单、限流、Pub/Sub、回答缓存、邀请令牌 | redis-py 同步单例 + 异步客户端（Pub/Sub） |
| MongoDB 7 | `agent_traces`、`deep_agent_tasks`、`analytics_events` | motor（异步）+ pymongo（同步），数据库名在代码中写死为 `zhihub` |
| Milvus 2.4 | `knowledge_chunks`、`user_memories` | pymilvus ORM 风格 API |
| Elasticsearch 8.13 | 索引 `knowledge_chunks` | elasticsearch-py |
| 本地文件卷 | 上传原始文件 `/data/uploads` | 直接文件读写 |

---

## 7. CI / 质量工具

`.github/workflows/ci.yml`（push 到 `main`/`develop`、PR 到 `main` 时触发）：

| 作业 | 步骤 |
|---|---|
| backend | 安装依赖 → `compileall` 语法检查 → `pip-audit` + 自定义门禁脚本 → 真实 MySQL 上 `alembic upgrade head` + `alembic check` → `pytest tests/unit tests/contracts --cov=app`（**不跑** `tests/integration` 与 `tests/e2e`；**无覆盖率阈值**） |
| frontend | `npm ci` → `npm audit --audit-level=high` → `vue-tsc --noEmit` → `vitest run` → `npm run build` |

没有镜像构建、镜像扫描、部署、回滚的流水线。

---

## 8. 扫描覆盖记录

| 范围 | 状态 |
|---|---|
| `backend/app` 全部目录结构 | ✅ 已扫描 |
| 精读文件 | `main.py`、`config.py`、`core/database.py`、`core/redis.py`、`core/idempotency.py`、`core/crypto.py`、`core/alipay_client.py`、`core/rabbitmq.py`、`core/ws_manager.py`、`api/deps.py`、`api/v1/session.py`、`api/v1/billing.py`、`api/v1/widget.py`（前半）、`api/v1/ws.py`（前半）、`api/v1/mcp/server.py`、全部 `middlewares/*`、全部 `models/*`（字段级）、`services/order_service.py`、`services/billing_service.py`、`services/human_service.py`、`services/agent_service.py`（主链路与流式部分）、`services/session_service.py`（上下文与在线坐席部分）、`services/mcp_service.py`（前 120 行）、`services/knowledge_service.py`（上传部分）、`services/tenant_service.py`（套餐与状态部分）、`services/trace_service.py`（连接部分）、`tasks/billing_tasks.py`、`tasks/cleanup_tasks.py`（租户清理部分）、`mcp_servers/order_server.py`（分发部分）、`celery_worker.py` |
| 按关键字扫描（未逐行精读） | `auth_service.py`、`retrieval_service.py`、`analytics_service.py`、`agents/*`、`tasks/knowledge_tasks.py`、`tasks/deep_agent_tasks.py`、其余 `core/*` 客户端 |
| `backend/alembic` | ✅ 迁移链（`alembic heads` / `history`）与方言相关 DDL |
| `backend/tests` | ✅ 结构、数量、夹具；✅ **实际运行全量测试与覆盖率** |
| `deploy/*`、`backend/Dockerfile`、`.github/workflows/ci.yml` | ✅ 全部 |
| `frontend` | 🔶 结构、依赖、API 调用层、测试清单；未逐个审阅 Vue 组件 |
| `docs/*` 已有文档 | 🔶 抽查 `README.md`、`docs/修复清单.md`、`docs/secret-rotation-runbook.md` |
