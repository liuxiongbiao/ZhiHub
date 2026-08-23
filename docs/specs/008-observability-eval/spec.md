# Feature Specification: 可观测性与效果评测

**Feature Branch**: `008-observability-eval`

**Created**: 2026-08-06

**Status**: Draft


## Clarifications

### Session 2026-08-06

- Q: Langfuse 用哪种部署方式？ -> A: 自托管 Docker（langfuse + postgres 两容器），与项目自托管中间件一致，验证时需 Docker。
- Q: RAGAS 评测用多大测试集？ -> A: 复用现有 eval_questions.md 18 题（4 类已覆盖），不扩展到 50。
- Q: RAGAS 评测如何保证指标稳定性？ -> A: 单次运行（温度 0），可复现靠固定知识库 + 固定 18 题 + 温度 0，不多次取均值。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - RAGAS 量化评测：混合检索 vs 纯向量 (Priority: P1)



**Independent Test**：运行评测脚本 -> 产出三指标（Faithfulness/AR/CR）的量化数值表，含纯向量 vs 混合对比，混合 Context Recall 高于纯向量。

**Acceptance Scenarios**：

1. **Given** 测试集（精确/语义/知识库外/混合四类问题），**When** 运行 RAGAS 评测，**Then** 产出每类问题在纯向量与混合检索下的 Faithfulness/Answer Relevancy/Context Recall 三项数值。
2. **Given** 评测完成，**When** 查看检索对比文档，**Then** 量化数据已填入，结论明确说明混合检索相对纯向量的提升幅度。
3. **Given** 评测脚本，**When** 重复运行，**Then** 结果可复现（同测试集 + 同知识库下指标稳定）。

---

### User Story 2 - Langfuse 全链路可观测 (Priority: P1)



**Independent Test**：发一条知识类消息 -> 在 Langfuse UI 看到该次执行的完整 4 段 trace（router/retrieval/agent/generation），含耗时。

**Acceptance Scenarios**：

1. **Given** 用户发送一条消息，**When** Agent 执行完成，**Then** Langfuse 出现一条 trace，含 router/retrieval/agent/generation 四段 span。
2. **Given** Langfuse trace，**When** 在 UI 查看，**Then** 可见每段耗时、输入（用户消息/检索 query）与输出（意图/检索结果/回答）。
3. **Given** trace 关联信息，**When** 按 session/tenant 检索，**Then** 可定位该会话的全部 trace。

---

### User Story 3 - trace PII 脱敏 + 90 天保留 (Priority: P2)

trace（MongoDB 与 Langfuse）入库前对手机号等 PII 脱敏（138****1234），原始 PII 不入库；MongoDB trace 设 90 天 TTL 自动过期。当前 trace 明文存储用户消息（含手机号），存在合规风险。

**Why this priority**：合规与隐私保护是可观测的必要前提，依赖 trace 链路已就绪（US2），优先级 P2。

**Independent Test**：发含手机号的消息 -> 查 MongoDB/Langfuse trace，手机号已脱敏；模拟 90 天后 trace 自动过期。

**Acceptance Scenarios**：

1. **Given** 用户消息含手机号 13800000001，**When** trace 入库，**Then** MongoDB 与 Langfuse 中的手机号显示为 138****0001，原始号不入库。
2. **Given** MongoDB trace 记录，**When** 超过 90 天，**Then** 自动过期删除。
3. **Given** 脱敏后的 trace，**When** 尝试还原，**Then** 无法还原（单向脱敏）。

---

### Edge Cases

- RAGAS 评测中 LLM 评分失败（API 超时/限流）：该题标记失败，不阻断整体评测，汇总时说明失败题数。
- 知识库外问题（无检索结果）：Context Recall 为 0 或 N/A，评测脚本需正确处理空检索。
- Langfuse 服务不可用（未启动/网络故障）：trace 写入降级跳过，不阻断 Agent 主流程（可观测是非关键路径）。
- 脱敏误伤：非手机号的 11 位数字（如订单号）被误脱敏 -> 脱敏规则限定手机号格式（1[3-9]\d{9} 整词匹配），避免误伤。
- MongoDB TTL 索引创建时机：首次写入前确保索引存在，否则旧数据不过期。
- RAGAS 指标在同测试集上的稳定性：LLM 评分有随机性，可设温度 0 + 多次取均值（plan 阶段定）。
- Langfuse trace 与 MongoDB trace 并存：同一执行写两份，需保证两者都不阻断主流程。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**：系统 MUST 接入 Langfuse，对每次 Agent 执行记录 trace，含 router/retrieval/agent/generation 四段 span，全链路可在 Langfuse UI 查看。
- **FR-002**：Langfuse trace MUST 关联 session_id/tenant_id/user_id，支持按会话与租户检索。
- **FR-003**：Langfuse 接入 MUST NOT 破坏现有 MongoDB trace，两者并存；任一写入失败 MUST NOT 阻断 Agent 主流程（降级跳过）。
- **FR-004**：系统 MUST 提供 RAGAS 评测脚本，对测试集计算 Faithfulness / Answer Relevancy / Context Recall 三项指标。
- **FR-005**：RAGAS 评测 MUST 分别对纯向量召回与混合检索运行，产出两者的量化对比数据。
- **FR-006**：RAGAS 评测结果 MUST 填入检索对比文档，含三指标量化数值、分类表现与混合检索提升幅度的结论。
- **FR-007**：trace（MongoDB 与 Langfuse）入库前 MUST 对手机号 PII 脱敏（前 3 后 4，如 138****0001），原始手机号 MUST NOT 入库。
- **FR-008**：脱敏 MUST 为单向（不可还原）；脱敏规则 MUST 限定手机号格式以避免误伤其他 11 位数字。
- **FR-009**：MongoDB trace MUST 设置 90 天 TTL 索引，超期记录自动过期删除。
- **FR-010**：Langfuse / RAGAS / 脱敏均属非关键路径，接入失败 MUST 降级跳过，MUST NOT 影响用户对话主流程。

### Key Entities

- **Langfuse Trace**：一次 Agent 执行的 span 树（router/retrieval/agent/generation），关联 session/tenant/user，含每段耗时与输入输出。
- **RAGAS 评测报告**：测试集 × {纯向量, 混合检索} × {Faithfulness, Answer Relevancy, Context Recall} 的量化数据矩阵。
- **脱敏规则**：手机号 -> 前 3 后 4（138****0001），单向，整词匹配 1[3-9]\d{9}。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：任意会话的 Agent 执行 100% 在 Langfuse UI 可见完整 4 段 trace（router/retrieval/agent/generation）。
- **SC-002**：RAGAS 评测产出 Faithfulness/Answer Relevancy/Context Recall 三项量化数值；混合检索 Context Recall 比纯向量提升 ≥ 15 个百分点（PRD 1.4 目标）。
- **SC-003**：trace 中手机号 100% 脱敏（138****0001），原始号不入库（MongoDB 与 Langfuse 均生效）。
- **SC-004**：MongoDB trace 超 90 天 100% 自动过期删除。
- **SC-005**：Langfuse/RAGAS/脱敏任一接入失败时，用户对话主流程 100% 不受影响（降级跳过）。

## Assumptions

- Langfuse 采用自托管（Docker，langfuse + postgres 两容器，与项目现有自托管中间件一致）；实现 SDK 接入代码不需 Docker，验证 trace UI 时需启动 Docker。
- RAGAS 评测脚本独立运行（非常驻请求路径），跑一次产出数据；评测需 Milvus/ES（Docker）+ LLM（DeepSeek API），到时提示用户启动 Docker。
- 现有 `docs/eval_questions.md` 18 题作为评测集（4 类已覆盖，已确认不扩展到 50）。
- 现有 `docs/retrieval_comparison.md` 已有 Top1 定性对比（83.3%），本 feature 在其上补 RAGAS 量化指标，不删除定性部分。
- 现有 `trace_service.py` 的 MongoDB trace 保留，本 feature 加 Langfuse + 脱敏 + TTL。
- 脱敏范围本 feature 仅手机号（最高频 PII）；邮箱/身份证等其他 PII 在文档口头标注生产补齐，不实现。
- RAGAS 评分用 LLM（DeepSeek），单次运行、温度 0；可复现性靠固定知识库 + 固定 18 题 + 温度 0，不多次取均值。
- Langfuse SDK 接入用 `langfuse` Python 包；RAGAS 用 `ragas` 包；均加入 requirements。