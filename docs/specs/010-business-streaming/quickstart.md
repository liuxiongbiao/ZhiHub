# Quickstart: 业务闭环与流式输出 验证指南

## 前置条件
- Docker: mysql/redis/milvus/es（US2 SSE 需 Milvus/ES + DeepSeek；US3 知识库需 Milvus/ES）
- 测试账号: 13800000001 / Test1234

## 场景 1: 退款撤销/分级/超时
```bash
# 撤销
POST /api/v1/order/refunds/{id}/cancel  # pending -> cancelled
# 大额（>1000）agent 批准 -> 仍 pending（待 admin）
# 大额 admin 批准 -> refunded
# 超时 7 天 -> 自动 cancelled
```

## 场景 2: SSE 流式
```bash
POST /api/v1/session/message/stream
{"session_id": <sid>, "content": "如何升级套餐？"}
# 预期: text/event-stream, data: {"chunk":"..."} 逐字, [DONE] 结束
```

## 场景 3: 知识库生命周期
```bash
# 上传乱码文档 -> quality_status=pending_review 不进检索
# 文档到期 -> is_latest=False 摘除
# 超 50 文档 -> 拒绝上传 403
```

## 场景 4: 计费补全
```bash
# 不同模型调用 -> 按权重折算
GET /api/v1/billing/usage/export  # CSV 下载
# 账单逾期 15 天 -> 降级（发消息返回降级提示）
```

## 验收对照
| SC | 场景 | 预期 |
|----|------|------|
| SC-001 | 1 | 撤销/分级/超时 100% |
| SC-002 | 2 | SSE 逐字输出，首字 <2s |
| SC-003 | 3 | 乱码/到期/超容量 100% |
| SC-004 | 4 | 折算/CSV/降级 100% |
| SC-005 | 全 | tenant_id 过滤 |