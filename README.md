# 智枢 ZhiHub

智枢 ZhiHub 是一套多智能体客服与运营中枢系统，面向企业级客服场景，提供「智能问答 + 业务系统集成 + 人工介入」的完整闭环能力。核心链路基于 LangGraph 显式状态图编排，确保每个业务动作可控、可审计。

> 状态说明：当前为内部验证版本。正式商用前需完成安全加固、依赖锁定与部署流水线验证，详见项目修复清单。

## 架构概览

```
用户 → Widget(对话) → Nginx → FastAPI → LangGraph(router→knowledge/ticket/order/human)
                                              ↓          ↓            ↓
                                        混合检索(Milvus+ES)  MCP Server  坐席工作台
                                              ↓
                                        LLM(DeepSeek) → 带引用回答
```

## 核心能力

| 能力 | 技术方案 | 主要模块 |
|---|---|---|
| LangGraph 多智能体编排 | StateGraph 节点 + 条件边 + 转人工中断 | `app/agents/graph.py` |
| 混合检索 | Milvus 向量 + ES BM25 + RRF 融合 + Rerank 精排 | `app/services/retrieval_service.py` |
| MCP 工具协议 | JSON-RPC 2.0 + 权限矩阵 + 幂等控制 | `app/mcp_servers/` `app/services/mcp_service.py` |
| Human-in-the-loop | Redis 分布式认领锁 + 转人工条件 | `app/services/human_service.py` |

## 快速启动

```bash
# 1. 启动全部中间件
cd zhihub/deploy
cp .env.example .env   # 按环境填写凭据，生产必须覆盖全部默认值
docker compose up -d --build
#    数据库迁移由 migration 服务自动执行；计费调度由 celery-beat 负责

# 2. 初始化 Milvus + ES
docker compose exec backend python -c "
from app.core.embedding_client import init_milvus_collection, init_es_index
init_milvus_collection(); init_es_index()
"

# 3. 数据库迁移（已由 migration 服务自动执行；如需手动补跑）
docker compose run --rm migration

# 4. 预置测试数据
docker compose exec backend python scripts/seed_data.py

# 5. 访问
open http://localhost  # Nginx → 前端 SPA
```

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 + FastAPI + LangGraph + SQLAlchemy 2.0 |
| 智能体 | DeepSeek（litellm 网关）+ 意图路由 + RAG 检索增强 |
| 数据 | MySQL（业务）+ Redis（缓存/锁/配额）+ MongoDB（trace）+ Milvus（向量）+ Elasticsearch（全文） |
| 异步 | Celery + RabbitMQ |
| 前端 | Vue 3 + TypeScript + Element Plus + Pinia |
| 部署 | Docker Compose + Nginx 反代 + GitHub Actions CI |

## 项目结构

```
zhihub/
├── backend/app/
│   ├── agents/          # LangGraph 图、节点与 DeepAgent
│   ├── services/        # 混合检索 / MCP / 工单 / 订单 / 会话 / 计费 / 人工介入
│   ├── mcp_servers/     # MCP 协议工单 / 订单服务
│   ├── api/v1/          # 路由层（auth / knowledge / session / ticket / order / billing / agent / mcp）
│   ├── models/          # ORM 模型
│   ├── crud/            # 数据访问层
│   ├── core/            # 安全 / Redis / 幂等 / Embedding / Rerank / 限流
│   └── middlewares/     # Auth / Tenant / RateLimit / CSRF
├── frontend/src/
│   ├── views/           # 页面组件
│   ├── components/      # 聊天 / 检索 / Trace / 反馈等组件
│   └── api/             # API 客户端模块
├── deploy/              # Docker Compose + Nginx
├── docs/specs/          # 特性规格文档（Spec-Kit，已入版本控制）
└── docs/                # 配置指南、评测集与评测报告
```

## 测试

```bash
cd backend
pytest tests/ -v
```

## 文档

- [外部服务与 API 密钥配置指南](docs/api-keys-guide.md)
- [检索效果评测集](docs/eval_questions.md)
- [混合检索评测对比报告](docs/retrieval_comparison.md)
- [智能体效果评测报告](docs/agent_eval_report.md)
- [连通性测试报告](docs/connectivity_test_report.md)
- [实现检查清单](docs/implementation_checklist.md)
