# Contract: 安全 API（双因子校验 + 注入检测）

> 本契约定义终端用户身份核验与 Prompt 注入检测的接口规范。

## 双因子校验流程（终端用户查询工单/订单）

### 触发点

终端用户通过对话查询工单/订单时，Agent 收集 `ticket_no`/`order_no` + `phone` 双槽位，齐全后调用 MCP 工具，工具内部校验身份。

### 校验逻辑（在 MCP server 层）

```
输入: ticket_no, phone, tenant_id
1. 按 (ticket_no, tenant_id) 查工单
2. 若工单不存在 -> 返回"未查询到匹配信息"（-32602）
3. 取工单绑定手机号，比对后4位 == phone 后4位
4. 不匹配 -> 返回"未查询到匹配信息"（-32602，不区分不存在与不匹配）
5. 匹配 -> 返回工单详情
```

### Agent 槽位收集（ticket_agent / order_agent）

- 槽位: `{ticket_no, phone}`（query_order 同理）
- 缺 ticket_no -> 反问"请提供工单号"
- 缺 phone -> 反问"请提供您的手机号"
- 双齐 -> 调 `call_tool(method, params={ticket_no, phone, tenant_id})`

### 边界

- 手机号格式非法（非 11 位数字）-> 反问格式
- 短信验证码方式（mock）: `phone + code`，code 校验 mock 化（任意 6 位数字通过），demo 阶段简化

## Prompt 注入检测

### 接口: `security_service.detect_prompt_injection(text) -> InjectionResult`

```python
@dataclass
class InjectionResult:
    is_risky: bool
    risk_level: str  # low / medium / high
    rule_hit: str | None  # 命中规则名
    action: str  # "allow" / "transfer_to_human"
```

### 规则库（初始集，可扩展）

| 规则名 | 模式 | 风险等级 | 动作 |
|---|---|---|---|
| ignore_instruction | `忽略.*指令` / `ignore.*previous.*instruction` | high | transfer_to_human |
| role_hijack | `你现在是` / `you are now` | high | transfer_to_human |
| system_prompt_leak | `系统提示词` / `system prompt` / `输出.*规则` | high | transfer_to_human |
| role_play | `扮演.*角色` / `pretend.*to be` | medium | allow（标记） |
| jailbreak_attempt | `越狱` / `jailbreak` / `DAN` | high | transfer_to_human |

### 调用点: `agent_service.handle_message`

```
1. 用户消息进入 -> 调 detect_prompt_injection(user_message)
2. 若 action == transfer_to_human:
   - 记录 SecurityIncident
   - 会话状态 -> waiting_human
   - 返回转人工提示，不进入 graph.invoke
3. 若 allow: 正常进入 graph 路由
```

### 安全事件记录: `security_service.record_incident(...)`

写入 `security_incidents` 表：tenant_id, session_id, user_message_preview（脱敏截断 500 字）, rule_hit, risk_level, action_taken。

## 误报处理

- 注入检测误报（正常查询被判风险）: action=transfer_to_human 时用户仍可向坐席申诉，坐席查看 trace 判断
- 不直接拒绝服务（blocked），始终转人工兜底（宪法原则 IV：用户体验兜底）