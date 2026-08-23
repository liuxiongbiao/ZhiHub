# Research: 业务闭环与流式输出

## R1: 退款撤销/分级审批
**Decision**: order_service 加 cancel_refund（pending->cancelled，乐观锁 WHERE status='pending'）；approve_refund 加金额分级（amount > REFUND_APPROVAL_THRESHOLD 默认 1000 -> 需 admin 角色，agent 角色批准的大额退款状态保持 pending 待二次确认）。
**Rationale**: 复用现有 _check_transition 乐观锁；分级不改状态机只加角色校验层。

## R2: 退款/文档超时
**Decision**: cleanup_tasks 加两段：退款 pending 超 7 天 -> cancelled；文档 expires_at 过期 -> is_latest=False + 摘除检索（Milvus delete + ES delete_by_query）。
**Rationale**: 复用 007 后台线程模式；退款超时用 UPDATE WHERE status='pending' AND created_at < threshold 原子条件。

## R3: SSE 流式
**Decision**: session.py 加 POST /message/stream，StreamingResponse；内部：router_node（意图）-> 若知识咨询则 hybrid_search 检索 -> litellm.completion(stream=True) 逐 chunk yield `data: {chunk}\n\n` -> 结束 `data: [DONE]\n\n`；完成后 save_message 落库。
**Rationale**: graph.invoke 返回完整 state 无法逐字流式；单独路径直接 litellm stream=True 首字延迟低（<2s）。非知识咨询（工单/订单）走同步兜底（流式仅知识类）。

## R4: 知识库质量检测
**Decision**: knowledge_tasks.process_document 切片后统计乱码率（非 UTF-8/非可读字符占比），超 30% -> quality_status='pending_review' 不进检索库（Milvus/ES 不插入）。
**Rationale**: 切片后检测，粒度合理；pending_review 文档可人工手动通过（改 status=ready 后手动入库）。

## R5: 存储上限
**Decision**: knowledge_service.upload 前查租户文档数（is_latest=True），超 50 -> 拒绝上传。
**Rationale**: count 查询简单可测；demo 50 文档够用。

## R6: 模型折算
**Decision**: config MODEL_WEIGHTS = {"deepseek/deepseek-chat": 1.0}；incr_usage 加 model 参数，count += weight（非 +1）。
**Rationale**: demo 只用 deepseek=1.0x（折算=原值），但代码展示折算机制；config 可扩展加模型。

## R7: CSV 导出
**Decision**: billing.py GET /usage/export，查 usage_records + 汇总 -> 返回 CSV（session_id/intent/model/tokens/weighted_tokens/created_at）。
**Rationale**: 复用现有 usage_records 表；CSV 用 csv 模块 + StreamingResponse。

## R8: 逾期降级
**Decision**: billing_service.check_overdue(db)：查 billing_invoice status='unpaid' 且 created_at < now-15天 -> tenant 状态标记 degraded（Redis key billing:degraded:{tenant_id}）；agent_service.handle_message 开头检查 degraded -> 返回降级提示。
**Rationale**: Redis 标记快速查询；降级在 handle_message 入口拦截，不影响其他端点。