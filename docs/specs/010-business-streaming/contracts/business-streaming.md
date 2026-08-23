# Contract: 业务闭环与流式输出

## 1. 退款撤销（US1）
**端点**: `POST /api/v1/order/refunds/{id}/cancel`
- 仅 status=pending(REFUNDING) 可撤销 -> CANCELLED
- 乐观锁 WHERE id=? AND status='pending'
- 404 不存在 / 409 已审批不可撤销

## 2. 退款分级审批（US1）
**修改**: `order_service.approve_refund`
- amount > REFUND_APPROVAL_THRESHOLD(1000) + role=agent -> 保持 pending（待 admin 二次审批）
- amount > 阈值 + role=admin -> REFUNDED
- amount <= 阈值 + role=agent/admin -> REFUNDED

## 3. 退款超时取消（US1）
**任务**: `cleanup_tasks.cleanup_overdue_refunds`（后台线程，每日跑）
- UPDATE refund_approvals SET status='cancelled' WHERE status='pending' AND created_at < now-7天

## 4. SSE 流式输出（US2）
**端点**: `POST /api/v1/session/message/stream`
- Content-Type: text/event-stream
- 流程: router_node -> 知识咨询则 hybrid_search -> litellm.completion(stream=True) yield chunks
- 非知识咨询（工单/订单）-> 同步兜底（一次性返回）
- 完成后 save_message 落库

## 5. 知识库质量检测（US3）
**修改**: `knowledge_tasks.process_document`
- 切片后统计乱码率 = 非可读字符 / 总字符
- 超 30% -> quality_status='pending_review'，不插入 Milvus/ES
- 正常 -> quality_status='ready'，插入检索库

## 6. 文档到期摘除（US3）
**任务**: `cleanup_tasks.cleanup_expired_docs`（后台线程，每日跑）
- 查 expires_at < now 且 is_latest=True -> is_latest=False + Milvus delete + ES delete_by_query

## 7. 存储上限（US3）
**修改**: `knowledge_service.upload`
- 上传前 count(is_latest=True, tenant_id) -> 超 50 -> 拒绝 403

## 8. 模型折算（US4）
**修改**: `billing_service.incr_usage(tenant_id, model)`
- count += MODEL_WEIGHTS.get(model, 1.0)（Redis INCRBYFLOAT 或 ZADD）

## 9. CSV 导出（US4）
**端点**: `GET /api/v1/billing/usage/export`
- 查 usage_records -> CSV StreamingResponse
- 字段: session_id, intent, model, tokens, weighted_tokens, created_at

## 10. 逾期降级（US4）
**修改**: `billing_service.check_overdue(db)` + `agent_service.handle_message`
- invoice unpaid + created_at < now-15天 -> Redis SET billing:degraded:{tenant_id}
- handle_message 开头检查 -> 降级提示