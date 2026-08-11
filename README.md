# 智枢 ZhiHub — 多智能体+RAG+MCP 企业级客服中枢

> 10天从零构建的可演示「多智能体+RAG+MCP」客服系统。LangGraph编排 × 混合检索 × MCP协议 × Human-in-the-loop × DeepAgent自主规划。

## 架构概览

```
用户 → Widget(对话) → Nginx → FastAPI → LangGraph(router→knowledge/ticket/order/human)
                                              ↓          ↓            ↓
                                        混合检索(Milvus+ES)  MCP Server  坐席工作台
                                              ↓
                                        LLM(DeepSeek) → 带引用回答
```

## 四条核心链路

| 链路 | 技术栈 | 文件 |
|---|---|---|
| LangGraph 多智能体编排 | StateGraph 5节点 + 条件边 + interrupt | `app/agents/graph.py` |
| 混合检索 (向量+BM25+RRF+Rerank) | Milvus(bge-m3) + ES(ik_smart) + bge-reranker | `app/services/retrieval_service.py` |
| MCP 真实协议调用 | JSON-RPC 2.0 + 权限矩阵 + 幂等 | `app/mcp_servers/` `app/services/mcp_service.py` |
| Human-in-the-loop | Redis SETNX 分布式认领锁 + 3条件触发 | `app/services/human_service.py` |

## 快速启动

```bash
# 1. 启动全部中间件
cd zhihub/deploy
docker compose up -d

# 2. 初始化Milvus + ES
docker compose exec backend python -c "
from app.core.embedding_client import init_milvus_collection, init_es_index
init_milvus_collection(); init_es_index()
"

# 3. 数据库迁移
docker compose exec backend alembic upgrade head

# 4. 预置测试数据
docker compose exec backend python scripts/seed_data.py

# 5. 访问
open http://localhost  # Nginx → 前端SPA
```

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 + FastAPI + LangGraph 1.0 + SQLAlchemy 2.0 |
| 智能体 | DeepSeek (litellm网关) + Router意图识别 + RAG检索增强 |
| 数据 | MySQL(业务) + Redis(缓存/锁/配额) + MongoDB(trace) + Milvus(向量) + ES(全文) |
| 异步 | Celery + RabbitMQ |
| 前端 | Vue 3 + TypeScript + Element Plus + Pinia |
| 部署 | Docker Compose + Nginx 反代 + GitHub Actions CI |

## 项目结构

```
zhihub/
├── backend/app/
│   ├── agents/          # 🔴 LangGraph图+nodes+prompts+DeepAgent
│   ├── services/        # 🔴 混合检索/MCP/工单/订单/会话/计费/HITL
│   ├── mcp_servers/     # 🔴 MCP协议ticket/order Server
│   ├── api/v1/          # 🟢 路由层(auth/knowledge/session/ticket/order/billing/agent/mcp)
│   ├── models/          # 🟢 12个ORM模型
│   ├── crud/            # 🟢 数据访问层
│   ├── core/            # 🔴 安全/Redis/幂等/Embedding/Rerank/限流
│   └── middlewares/     # 🟡 Auth/Tenant/RateLimit
├── frontend/src/
│   ├── views/           # 10个页面组件
│   ├── components/      # 聊天/检索/Trace/反馈等组件
│   └── api/             # 7个API模块
├── deploy/              # Docker Compose + Nginx
├── specs/               # Spec-Kit SDD文档(001-005)
└── docs/                # 评测问题集 + 检索对比表
```

## 测试

```bash
cd backend
pytest tests/ -v  # 166 passed, 9 skipped (需Docker中间件)
```

## 面试材料

- [6场景演示脚本](docs/demo_script.md)
- [面试追问预案](docs/interview_qa.md)
- [检索效果评测(18条)](docs/eval_questions.md)
- [纯向量vs混合检索对比](docs/retrieval_comparison.md)
