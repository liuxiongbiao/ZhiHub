# Implementation Plan: LangGraph 智能体主链路骨架

**Branch**: `003-langgraph-agent-chain` | **Date**: 2026-08-04 | **Spec**: [spec.md](./spec.md)

## Summary

实现 LangGraph 状态图编排骨架：路由节点（意图识别）→ 知识 Agent 节点（检索+生成带引用回答）。本日是"多智能体+RAG"核心叙事的组装日，将 Day 2 Auth 体系、Day 3 混合检索引擎、LangGraph 状态机三者串联。

## Technical Context

**Language/Version**: Python 3.14 + Vue 3 (TypeScript)

**Primary Dependencies**: FastAPI, LangGraph 1.0+, SQLAlchemy 2.0, litellm, Redis

**Storage**: MySQL (Session/Message ORM), Redis (会话上下文缓存)

**Testing**: pytest (后端) + Vitest (前端)

**Target Platform**: Linux server (Docker Compose)

**Project Type**: Web application (FastAPI backend + Vue 3 SPA frontend)

**Performance Goals**: 消息到回复全链路 <10s p95, 路由 LLM 调用 <3s, 会话列表分页 <2s

**Constraints**: Token 截断预算 4000 tokens/会话, 单会话 50 轮上限, 30min 空闲超时

**Scale/Scope**: 单租户 100+ 并发会话 (demo)

## Constitution Check

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Layered Boundaries | ✅ PASS | API→agent_service→graph nodes→retrieval_service / LLM；graph nodes 不直接操作 DB |
| II. Tenant Isolation | ✅ PASS | Session/Message 查询全部按 tenant_id 过滤；tenant_id 从 request.state 注入 |
| III. Money & State Safety | ⏭️ N/A | Day 4 不涉及资金/订单操作 |
| IV. End-to-End Acceptance | ✅ PASS | Widget 发知识问题 → 路由 → 检索 → 带引用回答，验收标准明确 |
| V. Depth Over Breadth | ✅ PASS | LangGraph 主链路是四条不可砍核心链路之首，Day 4 深度实现 knowledge_agent 分支 |

**Gate Result**: ✅ 全部通过。

## Project Structure

### Source Code

```text
zhihub/backend/app/
├── agents/
│   ├── graph.py              # 🔴 LangGraph 状态图定义+编译
│   ├── nodes/
│   │   ├── router.py         # 🔴 路由节点（意图识别+槽位+情绪）
│   │   ├── knowledge_agent.py# 🔴 知识问答节点
│   │   ├── ticket_agent.py   # 🟢 骨架（占位回复）
│   │   └── order_agent.py    # 🟢 骨架（占位回复）
│   └── prompts/
│       ├── router.txt        # 🟢→🔴 Prompt 模板
│       └── knowledge.txt     # 🟢→🔴 Prompt 模板
├── services/
│   ├── agent_service.py      # 🔴 编排中枢
│   └── session_service.py    # 🟡 会话状态机
├── models/
│   ├── session.py            # 🟡 ORM
│   └── message.py            # 🟢 ORM
├── crud/session.py           # 🟢 CRUD
├── api/v1/session.py         # 🟢 路由
└── utils/tokenizer.py        # 🟢 Token 估算

zhihub/frontend/src/
├── views/widget-demo/WidgetPreview.vue  # 🟡 Widget 入口
├── components/chat/ChatWindow.vue       # 🟡 聊天窗口
├── components/chat/MessageBubble.vue    # 🟢 消息气泡
└── types/session.d.ts                   # 🟢 类型定义
```

## Complexity Tracking

> 无违规项。
