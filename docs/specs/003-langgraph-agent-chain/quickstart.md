# Quickstart: LangGraph 智能体主链路骨架

**Phase 1 Output** | **Date**: 2026-08-04

## Prerequisites

1. Docker 中间件 healthy：MySQL, Redis（RabbitMQ/Milvus/ES 可选，知识库空也可以跑兜底回复）
2. Backend: `uvicorn app.main:app --port 8000`
3. Frontend: `npm run dev --port 5173`
4. DeepSeek API key 已配置

## Migration

```bash
cd zhihub/backend
alembic revision --autogenerate -m "day4_session_message"
alembic upgrade head
```

## 验收场景

### 场景 1: Widget 知识问答（对应 US1）

```bash
# 1. 创建会话
curl -X POST http://localhost:8000/api/v1/session \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"initial_message": ""}'

# 2. 发送知识问题
curl -X POST http://localhost:8000/api/v1/session/message \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"session_id": 1, "content": "如何升级套餐？"}'

# 预期: 返回带"来源："引用的回答，intent=knowledge_consult
```

### 场景 2: 非知识意图兜底（对应 US2）

```bash
curl -X POST http://localhost:8000/api/v1/session/message \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"session_id": 1, "content": "我的工单进度怎么样？"}'

# 预期: 返回兜底回复（工单查询即将上线），不报 500
```

### 场景 3: 前端 Widget 验收

打开 `http://localhost:5173/widget-demo` → 输入问题 → 看到带引用的回复 → 多次对话上下文连贯。

### 场景 4: 会话管理

```bash
# 列表
curl http://localhost:8000/api/v1/session/list?page=1 \
  -H "Authorization: Bearer $TOKEN"

# 详情
curl http://localhost:8000/api/v1/session/1 \
  -H "Authorization: Bearer $TOKEN"
```

## 运行测试

```bash
cd zhihub/backend
pytest tests/unit/test_graph.py tests/unit/test_router.py -v
pytest tests/integration/test_session_api.py -v
```
