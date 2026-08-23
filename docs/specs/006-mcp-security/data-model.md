# Data Model: MCP 协议化与智能体安全加固

## 新增 MySQL 表

### security_incidents

记录被拦截的 Prompt 注入尝试，供事后审查与规则优化。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT | PK, auto_increment | |
| tenant_id | BIGINT | NOT NULL, index | 租户归属（宪法原则 II） |
| session_id | BIGINT | nullable | 关联会话 |
| user_message_preview | VARCHAR(500) | NOT NULL | 触发规则的用户输入摘要（脱敏后） |
| rule_hit | VARCHAR(100) | NOT NULL | 命中的规则名（如 `ignore_instruction`） |
| risk_level | VARCHAR(20) | NOT NULL | low / medium / high |
| action_taken | VARCHAR(20) | NOT NULL | blocked / transferred_to_human |
| created_at | DATETIME | server_default now() | |

**索引**：`(tenant_id, created_at)` 用于按租户+时间查询事件。

## Redis 结构（非持久化表）

### `mcp:circuit:{tool_name}` -- 熔断状态

```json
{
  "state": "closed",
  "fail_count": 0,
  "recover_count": 0,
  "last_failed_at": null,
  "opened_at": null
}
```

| 字段 | 说明 |
|---|---|
| state | closed（正常）/ open（熔断中，返回兜底）/ half_open（探测中） |
| fail_count | 连续失败次数，达 5 转 open |
| recover_count | half_open 状态下连续成功次数，达 3 转 closed |
| last_failed_at | 最近一次失败时间戳 |
| opened_at | 进入 open 状态的时间戳，用于计算 half-open 超时（30s） |

**TTL**：1 小时（防止异常情况下熔断状态永久残留）。

## 数据结构（非实体）

### JSON-RPC 2.0 信封

**请求**（Agent -> MCP Server）：
```json
{
  "jsonrpc": "2.0",
  "id": "<uuid4>",
  "method": "get_ticket_status",
  "params": {
    "ticket_no": "TK-20240001",
    "phone": "13800000001",
    "tenant_id": 1
  }
}
```

**成功响应**：
```json
{
  "jsonrpc": "2.0",
  "id": "<uuid4>",
  "result": {
    "ticket_id": 1,
    "ticket_no": "TK-20240001",
    "status": "open",
    "title": "...",
    "priority": "high"
  }
}
```

**错误响应**：
```json
{
  "jsonrpc": "2.0",
  "id": "<uuid4>",
  "error": {
    "code": -32602,
    "message": "身份校验失败：手机号与工单不匹配"
  }
}
```

### 错误码定义（对齐 JSON-RPC 2.0 标准 + 业务扩展）

| 错误码 | 含义 | 触发场景 |
|---|---|---|
| -32700 | Parse error | 请求体非合法 JSON |
| -32600 | Invalid Request | 信封结构不合法（缺 jsonrpc/id/method） |
| -32601 | Method not found | 工具名不存在 |
| -32602 | Invalid params | 参数缺失/格式错/**身份校验失败** |
| -32603 | Internal error | server 内部异常 |
| -32000 | Permission denied | 权限矩阵拦截（角色无权调此工具） |
| -32001 | Tool circuit broken | 工具熔断中，返回兜底响应 |

## 现有表的影响

- `orders`：双因子校验读 `orders.phone` 字段（已存在），无需改表。
- `tickets`：工单绑定手机号需确认数据（当前 tickets 表若无 phone 字段，通过 applicant/创建者关联，plan 阶段确认；若无线索则在 server 层用 ticket_no + tenant_id 查询后与终端用户 phone 比对）。
- `refund_approvals` / `orders` 乐观锁/幂等：不受影响（协议化不改业务逻辑）。