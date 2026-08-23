# Quickstart: 可观测性与效果评测 验证指南

> 端到端验证 3 个 User Story 的核心场景，每个含异常分支（宪法 IV）。

## 前置条件

- **Docker 中间件**：mysql / redis / mongo / milvus / elasticsearch + **新增 langfuse + postgres**（本 feature 加）
- **后端依赖**：`.venv` 含 fastapi/sqlalchemy/langgraph；**新增** `pip install langfuse ragas langchain-openai`（加 requirements.txt）
- **.env 配置**：DeepSeek + 硅基流动 key（已有）+ `LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY`（自托管 Langfuse 启动后在 UI 创建 project 获取）
- **知识库**：已上传文档（manual.md 等），Milvus+ES 索引就绪（RAGAS 评测依赖）
- **测试账号**：13800000001 / Test1234

## 场景 1: RAGAS 量化评测（User Story 1）

### 1.1 跑评测脚本
```bash
cd zhihub/backend && .venv/Scripts/python.exe scripts/ragas_eval.py
# 预期: 对 18 题分别算纯向量/混合的 Faithfulness/AR/CR，打印进度
# 产出: docs/retrieval_comparison.md 追加"RAGAS 量化指标"表
```

### 1.2 验证量化结论
```bash
# 预期: 混合检索 Context Recall 比纯向量高 >=15 个百分点（SC-002）
# 验证: 打开 docs/retrieval_comparison.md 看 RAGAS 表 + 结论
```

### 1.3 异常：LLM 评分失败
```bash
# 模拟: 临时改错 DEEPSEEK_API_KEY 跑评测
# 预期: 题目标记 fail，不阻断整体，汇总报告含失败题数
```

## 场景 2: Langfuse 全链路 trace（User Story 2）

### 2.1 发消息产生 trace
```bash
# 启动 uvicorn（LANGFUSE_* 已配）
POST /api/v1/session/message
{"session_id": <sid>, "content": "如何升级套餐？"}
# 预期: 200 正常回复
```

### 2.2 Langfuse UI 查看
```bash
# 浏览器打开 http://localhost:3000（Langfuse UI）
# 预期: 看到 "agent.handle_message" trace，含 router/retrieval/agent/generation 四段 span
# 验证: 每段 span 有 input/output + 耗时；trace.metadata 含 session_id/tenant_id
```

### 2.3 异常：Langfuse 不可用
```bash
# 停掉 langfuse 容器，再发消息
# 预期: 对话正常 200（降级跳过 trace 上报），后端日志 warning
```

## 场景 3: trace PII 脱敏 + TTL（User Story 3）

### 3.1 脱敏验证
```bash
# 发含手机号的消息
POST /api/v1/session/message
{"session_id": <sid>, "content": "查工单 TK-20240001，手机号 13800000001"}
# 预期: 200
# 验证: MongoDB agent_traces 中该 trace 的 query/answer 手机号显示 138****0001，原始号不入库
# 验证: Langfuse UI 中 trace 的 input/output 同样脱敏
```

### 3.2 TTL 索引验证
```bash
# 检查 MongoDB 索引
mongosh zhihub --eval "db.agent_traces.getIndexes()"
# 预期: 含 created_at TTL 索引，expireAfterSeconds=7776000（90天）
```

### 3.3 异常：脱敏不误伤编号
```bash
# 发含工单号的消息
POST /api/v1/session/message
{"session_id": <sid>, "content": "查工单 TK-20240001"}
# 验证: trace 中 TK-20240001 不被脱敏（仅手机号脱敏）
```

## 连通性测试（每场景后跑）

```bash
cd zhihub/backend && .venv/Scripts/python.exe -c "import app.main; print('OK')"
.venv/Scripts/python.exe -c "
from app.core.langfuse_client import get_langfuse
from app.utils.desensitize import mask_pii
from app.services.trace_service import save_trace
print('mask_pii:', mask_pii('手机13800000001'))
print('all imports OK')
"
```

## 验收对照

| SC | 场景 | 预期 |
|----|------|------|
| SC-001 | 2.1/2.2 | 100% 会话在 Langfuse UI 可见完整 4 段 trace |
| SC-002 | 1.1/1.2 | RAGAS 三指标量化；混合 Context Recall - 纯向量 ≥15pp |
| SC-003 | 3.1 | trace 手机号 100% 脱敏（MongoDB + Langfuse） |
| SC-004 | 3.2 | MongoDB trace 90 天 TTL 索引就位 |
| SC-005 | 2.3/1.3 | Langfuse/RAGAS 失败时主流程 100% 不受影响 |