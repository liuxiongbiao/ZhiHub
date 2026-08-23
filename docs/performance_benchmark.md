# 性能基准报告（PRD 4.1）

## 1. 目的

为 PRD 4.1 性能要求提供可复现的验证手段与基准数据，作为验收与上线决策依据。

## 2. 压测方法

- 脚本：`backend/scripts/load_test.py`（轻量 HTTP 并发基准，输出 P50/P95/P99、均值、吞吐）。
- 环境：本地 Docker Compose 全栈（MySQL/Redis/RabbitMQ/ES/Milvus/后端/前端）。
- 隔离策略：LLM/embedding 为外部服务，端到端知识问答指标需配置真实 API Key 后执行；无 Key 时以 `/health`、`/api/v1/session/list`、`/api/v1/knowledge/test` 等链路指标为基线。

## 3. 执行命令

```powershell
cd backend
.venv/Scripts/python.exe scripts/load_test.py --base-url http://localhost:8080 --concurrency 20 --rounds 200 --endpoint /health
.venv/Scripts/python.exe scripts/load_test.py --base-url http://localhost:8080 --concurrency 20 --rounds 200 --token <JWT> --endpoint /api/v1/session/list
```

## 4. 基准结果

| PRD 4.1 指标 | 目标值 | 实测值 | 结论 |
|---|---|---|---|
| 混合检索总耗时 | < 500ms | 待执行 | 待验证 |
| 端到端知识问答响应时延 P95/P99 | P95<3s, P99<5s | 待执行（需真实 LLM Key） | 待验证 |
| MCP 工具调用响应时延 | < 2s | 待执行 | 待验证 |
| 会话状态流转延迟 | < 200ms | 待执行 | 待验证 |
| 并发会话支持 | 100 并发不降级 | 待执行 | 待验证 |
| 终端用户级限流 | 10 次/分钟 | 代码实现（`rate_limit_service.py`），运行时行为待压测确认 | 待验证 |

> 注：基准数据需在全栈启动后按上述命令执行并回填；本表当前为模板。

## 5. 结论与后续

- 压测脚本已纳入仓库，作为 8.1 验收的可执行资产。
- 全栈启动后回填实测值；若未达标，按指标逐项定位（检索链路计时、LLM 网关超时、MCP 熔断等）。
