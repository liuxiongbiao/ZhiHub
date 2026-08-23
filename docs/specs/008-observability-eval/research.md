# Research: 可观测性与效果评测

> Phase 0 产出：解决 Technical Context 中的未知项与技术选型。澄清决策见 spec.md `## Clarifications`。

## R1: Langfuse SDK 接入方式

**Decision**: 用 `langfuse` Python SDK v2 的 `@observe()` 装饰器，在 `agent_service.handle_message` 上加顶层 trace，子 span 包裹 router/retrieval/agent/generation 四段；trace 元数据带 session_id/tenant_id/user_id。

**Rationale**:
- `@observe()` 自动嵌套 span（装饰函数自动成为父 trace 的子 span），无需手工管理 span id
- Langfuse SDK 内部异步批量上报，不阻塞主流程；接入失败 SDK 自身降级（no-op）
- 现有 handle_message 已有清晰的四段结构（router invoke / retrieval / agent / generation），span 边界天然对齐
- Langfuse trace 与现有 MongoDB trace 并存：MongoDB trace 保留（已有前端 TraceTimeline），Langfuse 提供独立 UI + LLM 专用可观测

**Alternatives**:
- Langfuse LangChain 集成：项目用 litellm 非 langchain，不适用
- 手工 `lf.trace()` + span：代码量大、易错。否决，用 `@observe()`

## R2: Langfuse 自托管部署

**Decision**: docker-compose.yml 加 `langfuse` + `postgres` 两服务（Langfuse v3 自托管最小集）；config.py 加 `langfuse_host/public_key/secret_key`，从 .env 读。

**Rationale**:
- 与项目自托管中间件一致（mysql/redis/mongo/milvus/es 均自托管），数据本地、无外部账号
- Langfuse v3 自托管需 langfuse web + postgres（ClickHouse 可选，demo 砍掉用 postgres 内置）
- 实现阶段不需 Docker（SDK 代码 + 配置）；验证 trace UI 时需启动 Docker
- Langfuse 默认端口 3000，不与现有端口冲突

**Alternatives**:
- Langfuse Cloud：无 Docker 但需注册账号、数据上云。否决（与自托管一致性 + 数据本地）
- 不接 Langfuse 增强自建 trace UI：偏离 PRD 3.12.1，叙事弱。否决

## R3: RAGAS 评测脚本设计

**Decision**: `scripts/ragas_eval.py`，复用 `retrieval_service.hybrid_search`（一次调用同时返回 `vector_results` 与 `hybrid_results`，无需两趟检索），对 18 题分别取纯向量/混合的 contexts，用 DeepSeek 生成 answer，用 RAGAS 计算 Faithfulness/Answer Relevancy/Context Recall，结果写入 `docs/retrieval_comparison.md`（追加 RAGAS 量化表，不删现有 Top1 定性表）。

**Rationale**:
- `hybrid_search` 已同时产出 vector_results（纯向量 Top5）与 hybrid_results（混合 Top5），RAGAS 直接复用，零检索层改动
- RAGAS metrics 需 (question, contexts, answer, ground_truth)；ground_truth 用 eval_questions.md 的"预期"列简化为相关文档描述（demo 无人工标注答案，用预期描述作 reference）
- RAGAS 通过 langchain LLM 接口评分；用 `ChatOpenAI` 指向 DeepSeek base_url（litellm 兼容方式或 langchain-openai）
- 单次运行、温度 0（澄清 Q3）；LLM 评分失败该题标记 fail，不阻断整体

**关键决策**:
- ground_truth 来源：eval_questions.md"预期"列（定性描述）-> 作为 RAGAS 的 reference。Context Recall 依赖 ground_truth，定性描述够 demo 用
- 评分 LLM：DeepSeek（与主链路一致，温度 0）
- 输出：retrieval_comparison.md 追加"RAGAS 量化指标"表 + 结论（混合 Context Recall - 纯向量 Context Recall ≥ 15pp）

**Alternatives**:
- 人工标注标准答案：成本高、demo 不值。用"预期"列描述作 reference
- 多次取均值：成本 3×，澄清 Q3 已否决
- 跑两趟独立检索（纯向量/混合）：hybrid_search 已同趟产出，无需。否决

## R4: PII 脱敏实现

**Decision**: `app/utils/desensitize.py` 的 `mask_pii(text)`，正则 `1[3-9]\d{9}` 整词匹配手机号 -> 前3后4（138****0001），单向不可逆；在 `trace_service.save_trace` 入库前对 query/answer_preview 脱敏，Langfuse 上报前同样脱敏。

**Rationale**:
- 手机号是本项目最高频 PII（登录/双因子/留言均含），优先脱敏
- 整词匹配 `1[3-9]\d{9}`（\b 边界）避免误伤订单号/工单号（TK-.../ORD-... 不匹配）
- 单向：脱敏后无法还原（前3后4 中间4位星号，11位手机号中间4位组合数足够）
- 复用于 MongoDB trace + Langfuse trace，DRY

**Alternatives**:
- 可逆脱敏（加密+密钥）：trace 需可审计还原，但 demo 不需可逆且密钥管理复杂。否决
- 全量 PII（邮箱/身份证）：本 feature 仅手机号（澄清默认），其余口头标注。否决扩范围

## R5: MongoDB TTL 90 天

**Decision**: `trace_service` 模块加载时（或首次 save_trace）对 `agent_traces` collection 建 TTL 索引：`created_at` 字段，`expireAfterSeconds=90*24*3600`。MongoDB 后台自动删除超期文档。

**Rationale**:
- TTL 索引是 MongoDB 原生过期机制，无需定时任务，零维护
- 索引建在 `created_at`（已有字段），幂等（create_index 重复调用安全）
- 90 天 = PRD 3.12.1 要求；超期自动删，不破坏业务数据（trace 是观测数据非业务）

**Alternatives**:
- 手动定时清理任务：额外维护、不如 TTL 原生。否决
- 不过期：trace 无限增长。否决（PRD 要求 90 天）

## R6: 非关键路径降级

**Decision**: 所有 Langfuse 调用 + 脱敏包裹在 try/except，失败 log warning 不 raise；RAGAS 是离线脚本，per-题 LLM 失败标记 fail 继续，汇总报告失败题数。Agent 主流程（对话）100% 不受可观测/评测影响。

**Rationale**:
- 可观测/评测是辅助功能，挂了不能影响用户对话（宪法 IV 体验兜底）
- Langfuse SDK 自身有降级，但应用层再包一层 try/except 保险（网络/配置错误）
- 脱敏失败：fallback 用截断（取前20字）而非原文，确保不泄露 PII

**Alternatives**:
- 严格模式（失败即报错）：可观测挂了对话也挂，违反优先级。否决