# Contract: MCP 协议（JSON-RPC 2.0）

> 本契约定义 Agent 与 MCP Server 之间的标准化交互协议。

## 传输

- **端点**：`POST /api/v1/mcp/tools/call`（复用现有，响应体改为 JSON-RPC 信封）
- **Content-Type**: application/json
- **鉴权**：Bearer JWT（现有 AuthMiddleware 注入 role/tenant_id）

## 信封结构

见 [data-model.md](../data-model.md#json-rpc-20-信封)。

## 工具清单（method 定义）

### get_ticket_status

**params**:
```json
{"ticket_no": "TK-20240001", "phone": "13800000001", "tenant_id": 1}
```
- `ticket_no` (str, 必填): 工单号
- `phone` (str, 必填): 终端用户手机号（双因子校验）
- `tenant_id` (int, 服务端注入): 来自 JWT，客户端传入被覆盖

**result**:
```json
{"ticket_id": 1, "ticket_no": "TK-20240001", "status": "open", "title": "...", "priority": "high"}
```

**错误**:
- -32602: 身份校验失败（手机号与工单不匹配）/ 工单不存在（统一返回"未查询到匹配信息"）

### query_order

**params**:
```json
{"order_no": "ORD-20240001", "phone": "13800000001", "tenant_id": 1}
```
**result**:
```json
{"order_no": "ORD-20240001", "status": "paid", "amount": 299.0, "paid_amount": 299.0}
```
**错误**: 同上（手机号与订单不匹配）。

### create_ticket

**params**:
```json
{"title": "...", "description": "...", "priority": "high", "client_request_id": "<uuid>", "tenant_id": 1}
```
**result**: `{"ticket_id": 6, "ticket_no": "TK-20240006"}`

### update_ticket

**params**:
```json
{"ticket_no": "TK-20240001", "new_status": "in_progress", "note": "...", "client_request_id": "<uuid>", "tenant_id": 1}
```
**result**: `{"ticket_no": "TK-20240001", "status": "in_progress"}`

### request_refund

**params**:
```json
{"order_no": "ORD-20240001", "amount": 100.0, "reason": "...", "client_request_id": "<uuid>", "tenant_id": 1}
```
**result**: `{"approval_id": 1, "order_id": 1, "amount": 100.0, "status": "pending"}`

**错误**:
- -32602: 退款金额超过订单实付 / 订单状态不支持退款
- -32000: 角色无权调用写操作

## 调用流程（mcp_service.call_tool 内部）

1. 解析请求信封（校验 jsonrpc/id/method）
2. 注入 tenant_id（覆盖 params 中客户端传入值，宪法原则 II）
3. 权限矩阵校验（role vs method）-> 不通过返回 -32000
4. 熔断检查（circuit_breaker）-> 熔断中返回 -32001 + 兜底响应
5. 入参校验（validate_params）-> 不通过返回 -32602
6. 幂等检查（写操作，client_request_id）-> 命中返回首次结果
7. 调 server handler 执行业务
8. 关键字段空值监控 -> 失败 fail_count++（达5熔断）
9. 封装成功/错误信封返回

## 工具清单端点

`GET /api/v1/mcp/tools/list` 返回：
```json
{"jsonrpc":"2.0","result":{"tools":[...工具清单...]}}
```