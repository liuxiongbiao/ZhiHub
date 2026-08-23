# Feature Specification: 知识库上传 + 混合检索引擎

**Feature Branch**: `002-knowledge-hybrid-retrieval`

**Created**: 2026-08-04

**Status**: Draft

**Input**: User description: "Day 3 — 知识库上传 + 混合检索引擎：上传一份文档，检索测试台能看到纯向量 vs 混合检索的召回差异。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 上传文档并完成索引 (Priority: P1)

管理员登录租户后台，上传一份 PDF / Word / Markdown 文档到知识库。系统接收文件后异步处理——解析文本、切片、调用云端 embedding 服务生成向量、将切片与向量分别写入全文检索引擎和向量数据库——处理完成后文档状态变为"已就绪"。

**Why this priority**: 文档入库是整个检索系统的数据入口，没有已入库的文档，混合检索、对比测试等所有后续功能都无法验证。

**Independent Test**: 上传一份测试文档 → 轮询文档状态直到变为"ready" → 验证向量库和全文索引中均有该文档的切片记录。

**Acceptance Scenarios**:

1. **Given** 管理员已登录且租户有效，**When** 上传一份有效 PDF 文件（≤10MB），**Then** 返回文档上传成功，文档状态为"pending"，异步任务已投递。
2. **Given** 文档处于"pending"状态且异步处理已完成，**When** 轮询文档状态，**Then** 文档状态变为"ready"，且 chunk_count 字段 > 0。
3. **Given** 管理员已登录，**When** 上传一份无文本内容的空 PDF 文件，**Then** 文档状态变为"failed"，error_message 记录失败原因。
4. **Given** 管理员已登录，**When** 上传一份不支持的文件格式（如 .exe），**Then** 立即返回 400 错误，不创建文档记录。

---

### User Story 2 - 混合检索并对比纯向量召回 (Priority: P1)

管理员在检索测试台输入一个查询问题，系统分别执行纯向量召回和混合检索（向量 + BM25 全文 + RRF 融合 + 精排），并排展示两路结果，管理员能直观看到混合检索相比纯向量召回的改进。


**Independent Test**: 在已有至少一份"已就绪"文档的前提下，在测试台输入精确编号类问题（如"工单编号 TK-2024-001"）和语义模糊类问题（如"如何升级套餐"），分别观察两路结果的召回差异。

**Acceptance Scenarios**:

1. **Given** 租户已上传至少一份文档且状态为"ready"，**When** 在测试台输入一个精确关键词查询，**Then** BM25 召回结果排名靠前，混合检索最终排序优于纯向量召回。
2. **Given** 同上条件，**When** 输入一个语义模糊但意图明确的问题，**Then** 向量召回结果更相关，RRF 融合后的混合检索综合排名最优。
3. **Given** 租户未上传任何文档或所有文档状态均为非"ready"，**When** 执行检索，**Then** 返回明确标记"知识库为空"，不触发后端 LLM 兜底逻辑。
4. **Given** 已有文档且查询置信度极低的结果，**When** 检索结果置信度低于设定阈值，**Then** 触发 HyDE 兜底重检索，最终返回的标记中显示使用了兜底策略。

---

### User Story 3 - 文档列表与重新索引 (Priority: P2)

管理员在知识库文档列表页查看所有已上传文档（支持按状态筛选、分页），对已失败的文档可触发重新索引操作。

**Why this priority**: 文档管理是运营刚需——失败重试、版本更新都依赖列表页的可见性和操作入口。

**Independent Test**: 上传多份文档 → 在列表页按"processing"状态筛选 → 确认只显示对应状态文档 → 对一份"failed"文档点击"重建索引"→ 文档状态回到"pending"并重新进入队列。

**Acceptance Scenarios**:

1. **Given** 租户已上传多份不同状态的文档，**When** 请求文档列表（不传状态筛选），**Then** 返回该租户所有文档，按上传时间倒序排列，支持分页。
2. **Given** 存在一份状态为"failed"的文档，**When** 管理员点击"重建索引"，**Then** 文档状态重置为"pending"，新的异步任务被投递，且旧版本切片标记为 is_latest=false。

---

### User Story 4 - 版本管理：旧版本不进检索库 (Priority: P2)

同一租户重复上传同名文档时，新版本创建记录并设为 is_latest=true，旧版本归档（is_latest=false）且其切片数据不再进入检索结果。

**Why this priority**: 版本管理直接影响检索质量——如果不做旧版本隔离，矛盾/过时的信息会污染检索结果，导致 LLM 引用过期知识。

**Independent Test**: 上传文档 A → 再次上传同名文档 A → 验证旧版本的 is_latest=false → 在检索测试台确认只能搜到新版本的切片内容。

**Acceptance Scenarios**:

1. **Given** 租户已有一份"已就绪"的同名文档（v1），**When** 上传同名的更新版本文档，**Then** v1 的 is_latest 变为 false，新版本 v2 的 is_latest 为 true，检索时仅返回 v2 的切片。

---

### Edge Cases

- 上传超过 10MB 限制的文件时，必须在文件保存之前即拒绝，避免消耗磁盘和网络资源。
- 异步处理过程中 RabbitMQ 消息丢失或 Worker 崩溃：文档状态停留在"processing"超时后标记为"failed"。
- Embedding API 调用失败（网络超时 / API key 无效 / 返回 429 限流）：单切片失败应记录并继续处理其余切片，全部失败则标记文档为"failed"。
- Milvus 和 ES 双写不一致：一侧写入成功另一侧失败时，应重试失败侧（最多 3 次），重试仍失败则标记文档为"failed"并记录哪个存储写入失败。
- 并发上传同一文件：不做特殊去重（按 PRD 简化策略，每次上传创建独立的文档记录）。
- 检索时多个文档的相同内容片段：去重策略由 RRF + rerank 自然处理，不做额外的语义去重。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 支持上传 PDF (.pdf)、Word (.docx)、Markdown (.md) 三种格式的文档。
- **FR-002**: 系统 MUST 在上传时校验文件大小（≤10MB）和格式白名单，不符合要求立即拒绝。
- **FR-003**: 系统 MUST 在文件通过校验后创建 KnowledgeDocument 记录（status=pending），并投递异步任务到消息队列。
- **FR-004**: 异步 Worker MUST 依次执行：文件解析（按标题/段落规则切片）→ 调用 Embedding API → 并行写入向量数据库和全文检索引擎 → 更新文档状态（ready/failed）。
- **FR-005**: 系统 MUST 支持租户级别的数据隔离——所有知识库查询和检索操作都只返回当前租户的数据。
- **FR-006**: 检索接口 MUST 同时返回纯向量召回结果和混合检索结果，以便前端做对比展示。
- **FR-007**: 混合检索 MUST 按以下流程执行：查询改写（指代补全）→ 并行向量召回 Top20 + BM25 召回 Top20 → RRF 融合排序（k=60）→ Reranker 精排 Top5 → 置信度判断。
- **FR-008**: 当检索结果置信度低于阈值时，系统 MUST 触发 HyDE（假设文档嵌入）兜底策略重新检索一次。
- **FR-009**: 当租户知识库为空（无 ready 状态文档）时，检索 MUST 直接返回"未配置"标记，不触发 HyDE 兜底。
- **FR-010**: 文档版本管理：重复上传同名文档时，旧版本的 is_latest 字段 MUST 设为 false，且旧版本切片 MUST NOT 出现在检索结果中。
- **FR-011**: 系统 MUST 提供文档列表接口，支持按状态筛选和分页。
- **FR-012**: 系统 MUST 支持对已失败文档触发"重建索引"操作（重置状态为 pending 并重新投递异步任务）。
- **FR-013**: Worker 在 Milvus 和 ES 双写不一致时 MUST 重试失败侧（最多 3 次），全部重试失败则标记文档为"failed"。
- **FR-014**: Embedding API 调用失败时 MUST 记录错误日志，单切片失败不阻塞其余切片处理，全部切片失败则标记文档为"failed"。

### Key Entities

- **KnowledgeDocument**（知识库文档）：描述一份已上传的文档。关键属性包括所属租户、文件名、文件类型、大小、处理状态（pending/processing/ready/failed）、失败原因、版本号、是否为最新版本、切片数量、上传时间。
- **DocumentChunk**（文档切片）：文档经解析后拆分出的文本片段。在向量数据库中存储为向量+元数据（租户ID、文档ID、片段文本、版本号），在全文索引中存储为文本+元数据。切片在检索结果中展示其来源文档、片段文本、相似度/相关性分数。
- **RetrievalResult**（检索结果）：一次检索操作的输出，包含纯向量召回结果列表、混合检索结果列表、是否使用了 HyDE 兜底、检索耗时。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 管理员能在 30 秒内完成一份文档的上传操作（点击上传到看到状态变为 processing）。
- **SC-002**: 一份 1MB 以内的文档从上传到状态变为"ready"的端到端处理时间不超过 60 秒。
- **SC-003**: 在已有至少 3 份"ready"文档的知识库中，混合检索返回首条相关结果的准确率高于纯向量召回——精确关键词查询场景下混合检索的首条命中率提升明显。
- **SC-004**: 检索测试台能在 3 秒内返回一次混合检索的完整结果（查询改写 + 召回 + RRF + 精排全流程）。
- **SC-005**: 旧版本文档的切片在检索结果中的出现率为 0%（100% 隔离通过率）。
- **SC-006**: 双写失败重试场景验证通过：模拟一侧写入失败 → 系统自动重试 → 最多重试 3 次后标记失败。

## Assumptions

- 硅基流动的 Embedding API（bge-m3）和 Rerank API（bge-reranker-v2-m3）在开发期间可用且响应正常，API key 已配置在环境变量中。
- Milvus 和 Elasticsearch 在 Docker Compose 环境中已启动并通过健康检查（属于 Day 1 基础设施范畴）。
- RabbitMQ 消息队列可用，Celery Worker 已配置并能正常消费任务。
- 文档切片策略采用简化规则（按标题/段落分块，chunk_size≈512 tokens，chunk_overlap≈128 tokens），不引入 RagFlow 等重量级解析框架。
- 前端检索测试台仅用于开发/调试目的，不面向最终客户——UI 展示原始检索结果对比数据即可，不追求生产级美观。
- 多租户的租户上下文（tenant_id）已在请求中间件层注入 `request.state`，知识库模块从该可信来源读取，不信任客户端提交的 tenant_id。
