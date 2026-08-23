# Quickstart: 技术栈对比与智能体增强 验证指南

> 端到端验证 3 个 User Story 的核心场景，每个含异常分支（宪法 IV）。

## 前置条件

- **Docker 中间件**：mysql / redis / milvus / elasticsearch（US2 记忆需 Milvus；US1/US3 用 DeepSeek API）
- **.env**：DeepSeek + 硅基流动 key；`AGENT_TOOL_MODE=mcp|fc`（默认 mcp）
- **测试账号**：13800000001 / Test1234
- **知识库**：manual.md 已上传（FC/记忆查询可能用到）

## 场景 1: Function Calling 对比（User Story 1）

### 1.1 FC 模式查工单
```bash
# .env 设 AGENT_TOOL_MODE=fc，启动 uvicorn
POST /api/v1/session/message
{"session_id": <sid>, "content": "查工单 TK-20240001，手机号 13800000001"}
# 预期: 200，返回工单详情（与 MCP 模式一致）
# 验证: 后端日志见 litellm tool_calls -> mcp_service.call_tool（复用 006）
```

### 1.2 FC 双因子一致
```bash
POST /api/v1/session/message
{"session_id": <sid>, "content": "查工单 TK-20240001"}  # 缺 phone
# 预期: 反问手机号（与 MCP 一致）
```

### 1.3 对比文档
```bash
# 预期: ≥3 项差异 + 选型理由
```

## 场景 2: 长期记忆（User Story 2）

### 2.1 跨会话记忆
```bash
# 会话 A：表明偏好
POST /api/v1/session/message
{"session_id": <sidA>, "content": "我是企业版套餐用户"}
# 预期: 200，后端抽取 fact {plan: 企业版} 存 Milvus

# 会话 B（同用户新会话）：相关问题
POST /api/v1/session/message
{"session_id": <sidB>, "content": "我的套餐能升级吗？"}
# 预期: 回复体现"企业版"偏好（记忆被召回注入）
# 验证: Milvus memory_collection 有该用户记忆
```

### 2.2 记忆降级
```bash
# 停 Milvus，再发消息
# 预期: 对话正常（无记忆降级），日志 warning
```

## 场景 3: Agent 评测（User Story 3）

### 3.1 跑评测
```bash
cd zhihub/backend && .venv/Scripts/python.exe scripts/agent_eval.py
# 预期: 对 ~10 条测试集算意图准确率 + 工具调用成功率
# 产出: docs/agent_eval_report.md
```

### 3.2 验证报告
```bash
# 查 docs/agent_eval_report.md
# 预期: 意图识别准确率 X% + 工具调用成功率 Y% + 每条详情
```

## 连通性测试（每场景后跑）

```bash
cd zhihub/backend && .venv/Scripts/python.exe -c "import app.main; print('OK')"
.venv/Scripts/python.exe -c "
from app.agents.nodes.ticket_agent_fc import ticket_agent_fc_node
from app.services.memory_service import save_memory, search_memory, extract_facts
print('all imports OK')
"
```

## 验收对照

| SC | 场景 | 预期 |
|----|------|------|
| SC-001 | 1.1 | FC 模式查工单结果 100% 与 MCP 一致 |
| SC-002 | 1.3 | 对比文档 ≥3 差异 + 选型理由 |
| SC-003 | 2.1 | 会话 A 偏好在会话 B 100% 召回体现 |
| SC-004 | 3.1 | 评测产出意图准确率 + 工具成功率 |
| SC-005 | 全场景 | 记忆/评测带 tenant_id 过滤，无跨租户 |