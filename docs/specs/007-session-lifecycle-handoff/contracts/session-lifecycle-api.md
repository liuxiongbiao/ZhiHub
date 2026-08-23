# Contract: 会话生命周期 API（轮次转人工 / 留言建单 / CSAT / 限流 / 空闲收尾）

> 本契约定义 007 feature 涉及的端点与内部行为契约。

## 1. 轮次触发转人工（内部行为，无新端点）

**触发点**: `agent_service.handle_message` 末尾，每轮 AI 回复后 incr 轮次计数。

**逻辑**:
```
本轮 intent 来自 router 输出。rounds = get_rounds(session_id)
若 intent 为可解决类（ticket_query/order_query/sensitive_action）且 == rounds.last_intent:
    rounds.consecutive += 1
否则:
    rounds.consecutive = 0
rounds.total += 1
rounds.last_intent = intent
save_rounds(session_id, rounds)  # 续期 TTL

if rounds.consecutive >= 3 or rounds.total >= 15:
    should_interrupt = True  # 转人工（复用现有 should_interrupt -> waiting_human 链路）
    final_response = "已为您转接人工客服..."  # 转人工提示
```

**边界**:
- 知识咨询（knowledge_consult）不计入 consecutive（豁免多轮探索）
- 注入拦截（007 之前的 prompt_injection）已短路，不进轮次计数
- 坐席退回 AI（return_to_agent）时 reset consecutive=0，total 保留

## 2. 空闲超时收尾（内部定时任务，无端点）

**任务**: `cleanup_tasks.cleanup_idle_sessions`，后台线程每 5 分钟跑一次。

**逻辑**:
```
now = time.now()
# 阶段1: 活跃态 -> RESOLVED
UPDATE sessions SET status='resolved'
WHERE status IN ('agent_handling','waiting_human') AND updated_at < now-30min
# 阶段2: RESOLVED -> CLOSED
UPDATE sessions SET status='closed'
WHERE status='resolved' AND updated_at < now-60min
```
带 tenant_id 过滤（扫描全租户，但每条更新带 id+updated_at 乐观条件防竞态）。

## 3. 在线坐席 presence（内部，无端点）

- `auth_service.login`: 若 role==agent，SADD `agents:online:{tenant_id}` user_id
- `auth_service.logout`: 若 role==agent，SREM `agents:online:{tenant_id}` user_id
- `human_service.has_online_agent(tenant_id)`: SCARD == 0 即无在线坐席

## 4. 留言建单

**端点**: `POST /api/v1/session/{session_id}/leave-message`

**鉴权**: Bearer JWT（终端用户身份，role 无限制）

**请求**:
```json
{"content": "留言内容"}
```

**逻辑**:
1. 校验 session 归属当前 tenant（JWT tenant_id == session.tenant_id）
2. 校验 session 状态为 waiting_human（转人工态才可留言）
3. `has_online_agent(tenant_id)`：有在线坐席 -> 拒绝（提示等待坐席认领）；无 -> 继续
4. 调 `leave_message_if_no_agent`，建工单 `category="message"`，title=`会话{sid}留言`
5. 返回工单号

**响应**（200）:
```json
{"ticket_no": "TK-XXXXXXXX", "session_id": 123}
```

**错误**:
- 404: 会话不存在
- 403: 会话不属于当前租户 / 会话非 waiting_human 状态
- 409: 有在线坐席，无需留言

## 5. CSAT 满意度评价

**端点**: `POST /api/v1/session/{session_id}/csat`

**鉴权**: Bearer JWT

**请求**:
```json
{"score": 5, "comment": "可选文字"}
```
- score: 1-5（必填）
- comment: 可选，<=500 字

**逻辑**:
1. 校验 session 归属当前 tenant
2. 校验 session 状态为 closed（仅 CLOSED 可评价）
3. 查 csat_evaluations by session_id：已存在 -> 拒绝（同一会话仅一次）
4. 创建 CsatEvaluation（session_id, score, comment）

**响应**（201）:
```json
{"id": 1, "session_id": 123, "score": 5}
```

**错误**:
- 404: 会话不存在
- 403: 会话非 closed 状态
- 409: 该会话已评价

## 6. 终端用户对话限流

**作用点**: `POST /api/v1/session/message` 端点层（非 middleware）

**逻辑**:
```
allowed = check_message_rate_limit(user_id)  # 10次/分钟，键 rate:msg:{user_id}:minute
if not allowed:
    raise HTTPException(429, detail="您提问过于频繁，请稍后再试")
# 不落库消息记录、不计轮次
```

**响应**（429）:
```json
{"detail": "您提问过于频繁，请稍后再试"}
```

与现有 20/min 全局限流（middleware）并存：全局先拦 100/min 租户 / 20/min 用户，对话端点再拦 10/min。限流触发不进轮次计数、不写 messages 表。