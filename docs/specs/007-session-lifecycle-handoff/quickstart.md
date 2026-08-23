# Quickstart: 会话生命周期与转人工增强 验证指南

> 端到端验证 4 个 User Story 的核心场景，每个含异常分支（宪法 IV）。

## 前置条件

- 中间件运行: MySQL / Redis（docker compose up mysql redis）
- 后端依赖已装: `.venv` 含 fastapi/redis/sqlalchemy
- 已跑迁移: tickets 加 category 列（`d6_ticket_category` 或 init_db create_all 新表生效；现有 tickets 表需 ALTER 加列，与 006 phone 列同方式）
- 测试账号: `13800000001` / `Test1234`（admin, tenant_id=1）
- 至少一个 agent 角色账号（用于 presence 测试；可用 admin 注册第二个租户的 agent，或临时把 admin 当 agent）

## 场景 1: 轮次触发转人工（User Story 1）

### 1.1 连续3轮可解决类意图未解决 -> 转人工
```bash
# 同一 session 连发3次工单查询（意图不变 ticket_query）
POST /api/v1/session/message
{"session_id": <sid>, "content": "查工单 TK-20240001，手机号 13800000001"}
# 连发3次（每次同意图 ticket_query）
# 预期: 第3次后转人工，会话 status=waiting_human，回复含"转接人工"
# 验证: Redis session:rounds:{sid} consecutive>=3
```

### 1.2 累计15轮 -> 转人工
```bash
# 同一 session 累计发15条消息（可混合意图）
# 预期: 第15轮后转人工
# 验证: Redis session:rounds:{sid} total>=15
```

### 1.3 知识咨询不误触
```bash
# 连发3次知识咨询（knowledge_consult）
# 预期: 不转人工（consecutive 不计数知识咨询）
```

## 场景 2: 空闲超时收尾（User Story 2）

### 2.1 AGENT_HANDLING 30min 无活动 -> RESOLVED
```bash
# 手动把某 session updated_at 改到 31 分钟前（或等待）
# 触发 cleanup_idle_sessions（后台线程，或手动调一次）
# 预期: status: agent_handling -> resolved
```

### 2.2 RESOLVED 再 30min -> CLOSED
```bash
# 预期: status: resolved -> closed
```

### 2.3 竞态：用户消息优先
```bash
# 定时任务扫描时用户恰好发消息 -> updated_at 已更新 -> 跳过该会话
```

## 场景 3: 留言建单 + CSAT（User Story 3）

### 3.1 转人工无在线坐席 -> 留言建单
```bash
# 确保无 agent 在线（无 agent 登录，或 agent 登出）
# session 处于 waiting_human
POST /api/v1/session/{sid}/leave-message
{"content": "我下班后再联系"}
# 预期: 200, 返回 ticket_no; DB tickets 表新增 category=message 工单
```

### 3.2 有在线坐席 -> 拒绝留言
```bash
# agent 登录（SADD 在线集合）
POST /api/v1/session/{sid}/leave-message
# 预期: 409 有在线坐席
```

### 3.3 CSAT 评价
```bash
# session 状态置 closed
POST /api/v1/session/{sid}/csat
{"score": 5, "comment": "服务好"}
# 预期: 201; 重复提交 -> 409
# 非 closed 会话提交 -> 403
```

## 场景 4: 终端用户限流（User Story 4）

### 4.1 第11条/分钟 -> 429
```bash
# 同一用户 1 分钟内连发10条 /session/message -> 第11条:
POST /api/v1/session/message
# 预期: 429 {"detail":"您提问过于频繁，请稍后再试"}
# 验证: 第11条不落库 messages 表、不计轮次
```

### 4.2 不同用户互不影响
```bash
# 用户A达上限，用户B正常发消息 -> 200
```

## 连通性测试（每场景后跑）

```bash
cd zhihub/backend && .venv/Scripts/python.exe -c "import app.main; print('OK')"
.venv/Scripts/python.exe -c "
from app.services.session_service import incr_rounds, get_rounds, has_online_agent
from app.services.rate_limit_service import check_message_rate_limit
from app.tasks.cleanup_tasks import cleanup_idle_sessions
from app.crud.csat import create_csat, get_by_session
print('all imports OK')
"
```

## 验收对照

| SC | 场景 | 预期 |
|----|------|------|
| SC-001 | 1.1/1.2 | 连续3轮/累计15轮 100% 转人工；知识咨询不误触 |
| SC-002 | 2.1/2.2 | 30min 空闲 100% 收尾 RESOLVED；再30min CLOSED |
| SC-003 | 3.1/3.3 | 无坐席留言 100% 建单；CSAT 仅 closed 且不可重复 |
| SC-004 | 4.1/4.2 | 第11条/分钟 100% 429；不同用户互不影响 |
| SC-005 | 全场景 | 所有操作带 tenant_id 过滤，无跨租户影响 |