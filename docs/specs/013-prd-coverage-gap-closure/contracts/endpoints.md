# API Contracts: PRD 覆盖缺口闭环

> 仅列出本 feature 新增/修改的契约。既有端点（auth/sms/send、login/sms、register/email、login/email、password/reset、bind/email、wechat/*、session/claim、return-to-agent、trace、leave-message、csat、quick-replies、billing/pay、tenant/invite、audit-logs、widget、refunds/cancel）契约不变，前端补封装与调用。

## 后端新增/修改端点

### POST /api/v1/order/{order_id}/ship  [NEW]
- **权限**: 需登录（admin/agent，service 层不强制 role，路由级 _get_tenant_id）
- **请求体**: `{ "tracking_no": "string" }`（必填，1-100 字符）
- **成功**: 200 `{ "id": int, "order_no": str, "status": "shipped", "tracking_no": str }`
- **错误**: 404 订单不存在 | 400 非法状态跃迁（非 paid）| 409 乐观锁冲突 | 422 tracking_no 缺失/超长
- **契约测试**: paid 订单发货成功；shipped 订单发货 400；不存在 404；空 tracking_no 422。

### POST /api/v1/auth/register  [MODIFIED]
- **请求体**: `{ "phone": str, "password": str, "tenant_name": str, "code": str }`（新增 code 必填）
- **成功**: 201 RegisterResponse（不变）
- **错误**: 401 验证码无效/过期（InvalidCode）| 409 手机号已存在 | 422 密码弱
- **契约测试**: 正确验证码注册成功；错误验证码 401；缺 code 422。

### POST /api/v1/session/message/stream  [MODIFIED]
- **行为变更**: 先调 agent_service.handle_message 走 Agent 全链路，再 SSE 逐字输出 content。
- **响应**: SSE `data: {"chunk": "<char>"}` ... `data: [DONE]`；错误 `data: {"error": "..."}`
- **契约测试**: 响应内容来自 Agent（mock agent_service，断言被调用且 content 来自其返回）；不再直接调 litellm.completion。

## 前端 api 新增封装（契约对齐后端既有端点）

### auth.ts 新增
- `smsSend(phone)` -> POST /auth/sms/send -> `{message, code, expires_in}`
- `loginSms(phone, code)` -> POST /auth/login/sms -> LoginResponse
- `registerEmail(body)` -> POST /auth/register/email -> EmailRegisterResponse
- `loginEmail(email, password)` -> POST /auth/login/email -> LoginResponse
- `wechatAuth(redirect_uri)` -> GET /auth/wechat/auth -> `{auth_url}`
- `wechatCallback(code, state)` -> GET /auth/wechat/callback -> WechatCallbackResponse
- `register` 改为携带 code: `register({phone, password, tenant_name, code})`

### session.ts 新增
- `getTrace(sessionId)` -> GET /session/{id}/trace -> `{session_id, traces, total}`
- `leaveMessage(sessionId, content)` -> POST /session/{id}/leave-message -> `{ticket_no, session_id}`
- `submitCsat(sessionId, score, comment?)` -> POST /session/{id}/csat -> `{id, session_id, score}`
- `getQuickReplies()` -> GET /session/quick-replies -> `[{id, content, shortcut}]`
- `createQuickReply(content, shortcut?)` -> POST /session/quick-replies -> QuickReplyItem

### billing.ts 新增
- `payInvoice(invoiceId)` -> POST /billing/pay/{id} -> `{trade_no, pay_url}` （后端 create_payment 返回）

### tenant.ts 新增
- `inviteMember(phone)` -> POST /tenant/invite -> `{invite_url, token}` （InviteResponse）
- `getAuditLogs(page?, page_size?)` -> GET /tenant/audit-logs -> `{items, total, page, page_size}`
- `updateWidgetConfig(body)` -> POST /tenant/widget -> WidgetConfigResponse
- `getWidgetEmbedCode()` -> GET /tenant/widget/embed-code -> `{tenant_id, embed_code}`

### order.ts 新增/修改
- `shipOrder(orderId, tracking_no)` -> POST /order/{id}/ship -> `{id, order_no, status, tracking_no}`
- `cancelRefund(approvalId)` -> POST /order/refunds/{id}/cancel -> refund 结果
- `Order.status` 改为字面量联合 + STATUS_MAP 中文映射
- `RefundApproval.status` 补全枚举 + 中文映射


## 前端 WS / SSE 契约（US7/US8）

### WS /api/v1/ws/agent?token=<JWT>  [US8]
- **连接**: `new WebSocket('ws://host/api/v1/ws/agent?token=' + token)`（生产 wss://）
- **鉴权失败**: close code=1008
- **服务端推送**: `{"type":"new_session","session_id":N,"status":"waiting_human",...}` / `{"type":"typing","session_id":N,"is_typing":bool}`
- **客户端发送**: `{"type":"typing","session_id":N,"is_typing":true}`

### SSE POST /session/message/stream  [US7]（后端已复用 Agent）
- **请求**: `{session_id, content}`
- **响应**: `data: {"chunk":"<char>"}\n\n` ... `data: [DONE]\n\n` / `data: {"error":"..."}\n\n`
- **前端消费**: fetch + ReadableStream reader 逐行解析 `data: ` 前缀
