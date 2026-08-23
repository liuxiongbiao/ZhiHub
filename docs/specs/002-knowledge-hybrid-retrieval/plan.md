# Implementation Plan: 知识库上传 + 混合检索引擎

**Branch**: `002-knowledge-hybrid-retrieval` | **Date**: 2026-08-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from [spec.md](./spec.md)

## Summary

实现知识库文档上传、异步处理（解析→embedding→双写Milvus+ES）和混合检索引擎（向量召回+BM25→RRF融合→rerank精排）。这是全项目技术含量最高的单日，`retrieval_service.py` 为核心文件。

## Technical Context

**Language/Version**: Python 3.14 + Vue 3 (TypeScript)

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0, Celery + RabbitMQ, pymilvus, elasticsearch-py, httpx, langchain, litellm

**Storage**: MySQL (业务元数据), Milvus (向量), Elasticsearch (全文索引), Redis (缓存/锁)

**Testing**: pytest (后端) + Vitest (前端)

**Target Platform**: Linux server (Docker Compose), 开发环境 Windows 11 + WSL/Bash

**Project Type**: Web application (FastAPI backend + Vue 3 SPA frontend)

**Performance Goals**: 检索全流程 <3s p95, 文档异步处理 <60s/1MB, 并发上传队列无丢失

**Constraints**: Embedding API 调用限流重试; Milvus+ES 双写原子性保证（重试3次）；多租户隔离不可串数据

**Scale/Scope**: 单租户 100+ 文档, 10k+ 切片, 检索并发 <10 QPS (demo 阶段)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Layered Boundaries | ✅ PASS | API 路由 → knowledge_service → crud/knowledge + tasks/knowledge_tasks; retrieval_service 不直接操作 DB，通过 crud + Milvus/ES client 抽象 |
| II. Tenant Isolation | ✅ PASS | 所有检索查询带 `tenant_id` 过滤；tenant_id 从 `request.state`（中间件注入）获取，不信任客户端提交；Milvus partition key + ES filter 双重保障 |
| III. Money & State Safety | ⏭️ N/A | Day 3 知识库模块不涉及资金/订单操作 |
| IV. End-to-End Acceptance | ✅ PASS | 验收标准已在 spec.md 定义 6 条 Success Criteria + 每条 User Story 的 Acceptance Scenarios；包含空知识库、Embedding API 失败、双写不一致等异常分支 |
| V. Depth Over Breadth | ✅ PASS | 混合检索（Milvus+ES+RRF+rerank）是四条不可砍核心链路之一，Day 3 聚焦将其做深做透 |

**Gate Result**: ✅ 全部通过，进入 Phase 0。

## Project Structure

### Documentation (this feature)

```text
specs/002-knowledge-hybrid-retrieval/
├── plan.md              # This file
├── research.md          # Phase 0 output — 技术决策记录
├── data-model.md        # Phase 1 output — 数据模型设计
├── quickstart.md        # Phase 1 output — 验收验证指南
├── contracts/           # Phase 1 output — API contracts
│   └── knowledge-api.yaml
└── tasks.md             # Phase 2 output (/speckit-tasks, NOT created by plan)
```

### Source Code (repository root)

```text
zhihub/
├── backend/
│   ├── app/
│   │   ├── api/v1/knowledge.py          # 路由：upload/list/rebuild/test
│   │   ├── core/
│   │   │   ├── embedding_client.py      # NEW: 硅基流动 bge-m3 Embedding API 封装
│   │   │   └── rerank_client.py         # NEW: 硅基流动 bge-reranker-v2-m3 API 封装
│   │   ├── crud/knowledge.py            # 文档元数据 CRUD
│   │   ├── models/knowledge_document.py # ORM: KnowledgeDocument
│   │   ├── schemas/knowledge.py         # Pydantic: 请求/响应模式
│   │   ├── services/
│   │   │   ├── knowledge_service.py     # 上传校验 + 状态流转
│   │   │   └── retrieval_service.py     # 🔴 核心：混合检索编排
│   │   ├── tasks/knowledge_tasks.py     # Celery: 异步解析+embedding+双写
│   │   └── utils/
│   │       └── file_parser.py           # 文件类型检测 + 规则切片
│   └── tests/
│       ├── unit/
│       │   ├── test_file_parser.py
│       │   └── test_retrieval_service.py
│       └── integration/
│           ├── test_knowledge_api.py
│           └── test_knowledge_tasks.py
├── frontend/
│   ├── src/
│   │   ├── api/knowledge.ts             # API 接口层
│   │   ├── views/knowledge/
│   │   │   ├── KnowledgeList.vue        # 文档列表页
│   │   │   └── SearchTest.vue           # 检索测试台
│   │   └── components/trace/
│   │       └── RetrievalResult.vue      # 检索结果卡片
│   └── tests/
│       └── components/
│           ├── KnowledgeList.spec.ts
│           └── SearchTest.spec.ts
```

**Structure Decision**: 选择后端+前端的 Web 应用结构，匹配现有 `zhihub/backend/` 和 `zhihub/frontend/` 目录。新增两个 core 模块（embedding_client / rerank_client）封装硅基流动 API。

## Complexity Tracking

> 无违规项，无需记录。
