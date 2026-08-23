# Implementation Plan: 业务闭环与流式输出

**Branch**: `010-business-streaming` | **Date**: 2026-08-06 | **Spec**: [spec.md](./spec.md)

## Summary

四项后端收尾：(1) 退款撤销/分级审批/超时（cancel_refund + amount>1000 二次审批 + 7天超时取消）；(2) SSE 流式输出（POST /message/stream，单独路径 litellm stream=True yield SSE）；(3) 知识库生命周期（+expires_at/quality_status，质量检测+到期摘除+存储上限 50 文档）；(4) 计费补全（config 模型权重折算 + CSV 导出 + 逾期 15 天降级）。最后一个 feature，后端全收尾。

## Technical Context

**Language/Version**: Python 3.14
**Primary Dependencies**: FastAPI, SQLAlchemy, Redis, litellm（现有）
**Storage**: MySQL（knowledge_documents 加 expires_at/quality_status 列）；Redis（计费折算）
**Testing**: pytest（TDD；SQLite+fakeredis for 单测；SSE/知识库/计费验证需 Docker+LLM）
**Constraints**: 退款撤销/分级不破坏现有 006 幂等/乐观锁；SSE 不影响同步端点；知识库改动不破坏现有检索
**Scale/Scope**: 修改 order_service/billing_service/knowledge_service/knowledge_tasks/session.py/knowledge_document 模型；新增 cleanup 超时任务 + SSE 端点 + CSV 导出

## Constitution Check

| Principle | Status | 说明 |
|-----------|--------|------|
| I. Layered Boundaries | ✅ | 退款/计费在 Service 层；SSE 端点在 API 层；知识库质量在 tasks 层；MCP/Agent 不直接操作 |
| II. Tenant Isolation | ✅ | 退款撤销/审批/导出/降级/存储上限均带 tenant_id 过滤 |
| III. Money & State Safety | ✅ | 退款撤销/分级审批保持乐观锁+幂等；超时取消用 UPDATE WHERE status='pending' 原子条件 |
| IV. E2E Acceptance | ✅ | 4 个 User Story 各有 Independent Test + 异常分支 |
| V. Depth Over Breadth | ✅ | 业务闭环 + 流式是 demo 完整度关键 |

## Project Structure

```text
backend/app/
├── services/
│   ├── order_service.py       # 修改：cancel_refund + approve_refund 分级
│   ├── billing_service.py     # 修改：incr_usage 加权重折算 + export_usage + check_overdue
│   └── knowledge_service.py   # 修改：上传时校验存储上限 + 质量检测
├── tasks/
│   └── cleanup_tasks.py       # 修改：加退款超时取消 + 文档到期摘除
├── api/v1/
│   ├── session.py             # 修改：POST /message/stream SSE 端点
│   ├── order.py               # 修改：POST /refunds/{id}/cancel 端点
│   └── billing.py             # 修改：GET /usage/export CSV
├── models/
│   └── knowledge_document.py  # 修改：+expires_at +quality_status
├── config.py                  # 修改：+MODEL_WEIGHTS +STORAGE_LIMIT +REFUND_TIMEOUT
backend/tests/
├── contracts/test_business_streaming.py
└── integration/test_business_streaming_e2e.py
```

## Complexity Tracking

| 新增/改动 | Why Needed | 更简替代被否原因 |
|---------|------------|-----------------|
| knowledge_document +expires_at/quality_status | 生命周期+质量检测需持久化字段 | Redis 不适合持久化元数据 |
| SSE 单独流式路径 | graph.invoke 无法逐字流式 | 假流式（先全量再分块）失去流式意义 |
| config MODEL_WEIGHTS | 多模型折算需可配置 | 硬编码不可扩展 |
| cleanup 退款超时+文档到期 | 复用 007 cleanup_tasks 后台线程模式 | 独立定时任务重复 |