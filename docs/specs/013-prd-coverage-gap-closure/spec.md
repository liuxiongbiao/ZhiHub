# Feature Specification: PRD 覆盖缺口闭环（前后端联调补齐）

**Feature Branch**: `013-prd-coverage-gap-closure`

**Created**: 2026-08-11

**Status**: Draft

**Input**: User description: "按照《智枢ZhiHub_前后端联调与PRD覆盖审查报告.md》修改项目，后端已实现但前端未对接的 P0 级端点全部接通，并补齐后端缺失的订单发货与流式端点复用 Agent，使 PRD 定义的核心闭环在 UI 层真正打通。使用 spec-kit SDD + TDD 流程。"

## 背景与范围

审查报告结论：后端 14 个 PRD 模块基本全部实现（49 个端点），但前端只接通约 25 个，**约 22 个业务端点前端未对接**，其中多个 P0 验收阻断项。本 feature 聚焦 P0 批次，将"后端已造、前端没接"的能力全部接通，并修复 3 处后端设计偏差（订单发货缺失、流式端点绕过 Agent、注册无短信验证），使 PRD 核心闭环（多方式登录、支付闭环、AI↔人工双向协作、CSAT、留言建单、实时推送）在端到端演示中可走通。

**不在本 feature 范围**（P1/P2 留待后续）：Widget 品牌定制配置页、审计日志页、密码重置/账号绑定个人中心、退款撤销、DeepAgent 实时步骤、订单看板视图。这些后端已就绪，前端入口可后续补。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 多方式登录与注册短信验证 (Priority: P1)

普通用户在登录页可通过"账号密码 / 手机验证码 / 微信扫码"三种方式以选项卡并列登录；注册时必须先获取并校验短信验证码才能完成手机号注册，并提供邮箱注册入口。当前仅密码登录 + 无验证码手机注册，验证码登录、微信扫码、邮箱注册、注册短信校验均不可用。

**Why this priority**: PRD 3.1.1/3.1.2 主推的多方式登录与注册是验收的入口能力，验证码登录与微信扫码完全不可用直接导致"多方式登录"验收不达标，注册无短信校验使手机号真实性无保障。

**Independent Test**: 可独立验证——打开登录页可见三个选项卡，切换到验证码登录输入手机号获取验证码后登录成功；注册页填写手机号后必须先点"获取验证码"并填入正确验证码才能提交注册；邮箱注册入口可用。

**Acceptance Scenarios**:

1. **Given** 用户未登录，**When** 在登录页切换到"手机验证码"选项卡并输入手机号点击"获取验证码"，**Then** 调用 /auth/sms/send 收到验证码，填入后点登录调用 /auth/login/sms 返回 token 并进入后台。
2. **Given** 用户在登录页，**When** 切换到"微信扫码"选项卡，**Then** 调用 /auth/wechat/auth 获取授权 URL 并展示二维码/跳转。
3. **Given** 新用户注册，**When** 填写手机号点击"获取验证码"后填入正确验证码并提交，**Then** 调用 /auth/register（携带验证码）成功创建租户；若验证码错误则注册被拒。
4. **Given** 新用户选择邮箱注册，**When** 填写邮箱+密码+企业名提交，**Then** 调用 /auth/register/email 成功创建账户。

---

### User Story 2 - 支付入口闭环 (Priority: P1)

管理员在账单列表中对未支付账单点击"支付"按钮，系统调用 /billing/pay/{id} 生成支付宝沙箱订单并跳转到沙箱支付页，支付回调后账单状态恢复为已支付。当前账单列表只展示状态，无支付按钮，支付闭环断链。

**Why this priority**: PRD 3.10 营利性演示的核心闭环（账单->支付->回调->恢复套餐）在 UI 层断开，无法演示支付流程，是验收阻断项。

**Independent Test**: 可独立验证——以 admin 登录进入账单列表，对一条 unpaid 账单点击"支付"，前端调用 pay 端点拿到跳转 URL（demo 环境可拦截跳转展示 URL），账单状态正确处理。

**Acceptance Scenarios**:

1. **Given** admin 在账单列表看到一条 unpaid 账单，**When** 点击该账单的"支付"按钮，**Then** 调用 POST /billing/pay/{invoice_id}，返回支付 URL，前端触发跳转（或 demo 模式展示 URL）。
2. **Given** 账单已 paid，**When** 查看账单列表，**Then** 该账单不显示"支付"按钮，仅显示"已支付"状态。
3. **Given** 非 admin 角色，**When** 访问账单列表，**Then** 被权限守卫拦截（已有路由守卫，pay 端点后端也限制 admin）。

---

### User Story 3 - 坐席工作台双向协作与 Trace (Priority: P1)

坐席在 AgentConsole 工作台能"认领"会话后，看到 Agent 执行轨迹时间线与建议方案；并能点击"退回给 AI"将会话退回 Agent 自动处理（状态回到 agent_handling）。当前坐席只能认领不能退回，且工作台不显示 Agent Trace 与建议，"AI↔人工双向协作"退化为单向。

**Why this priority**: PRD 3.7 双向协作与"缩短接管响应时间"是 HITL 核心价值点，退回 Agent 断链使协作单向，Trace 缺失使坐席从零开始。

**Independent Test**: 可独立验证——以 agent 登录工作台，认领一条 waiting_human 会话后，右侧出现 Agent Trace 时间线；点击"退回给 AI"按钮，会话状态回到 agent_handling，列表刷新后该会话离开待认领。

**Acceptance Scenarios**:

1. **Given** agent 在工作台认领了一条会话，**When** 查看右侧面板，**Then** 显示 Agent 执行轨迹时间线（调用 GET /session/{id}/trace）。
2. **Given** agent 已认领会话，**When** 点击"退回给 AI"按钮，**Then** 调用 POST /session/{id}/return-to-agent，会话状态回到 agent_handling，列表刷新后该会话不再处于坐席处理中。
3. **Given** 会话未认领，**When** 查看操作区，**Then** 不显示"退回给 AI"按钮（仅认领后才出现）。

---

### User Story 4 - CSAT 满意度评价与留言建单 (Priority: P1)

会话关闭（CLOSED）后向用户弹出 CSAT 满意度评价（1-5 星）；当转人工但无在线坐席时，用户端显示"留言"入口，提交后系统自动创建工单记录诉求。当前 CSAT 与留言建单前端均无任何调用。

**Why this priority**: PRD 3.7 服务质量度量（CSAT）与无坐席兜底（留言建单）是 HITL 路径体验评估与兜底机制，缺失使服务质量无法采集、非工作时间诉求无处留存。

**Independent Test**: 可独立验证——会话状态为 closed 时弹 CSAT 评价组件，提交调用 /session/{id}/csat 成功；会话处于 waiting_human 且无在线坐席时显示留言入口，提交后调用 /session/{id}/leave-message 返回工单号。

**Acceptance Scenarios**:

1. **Given** 会话状态为 closed，**When** 用户查看会话，**Then** 弹出 CSAT 评价组件（1-5 星 + 可选文字）。
2. **Given** 用户填写评分并提交，**When** 点击提交，**Then** 调用 POST /session/{id}/csat，成功后提示感谢；重复提交被 409 拒绝。
3. **Given** 会话处于 waiting_human 且无在线坐席，**When** 用户看到留言入口并提交留言，**Then** 调用 POST /session/{id}/leave-message，返回工单号，提示已记录。

---

### User Story 5 - 成员邀请、首页告警与快捷回复 (Priority: P1)

管理员可邀请坐席成员（受 seat_limit 约束）；首页渲染配置向导完成度与系统降级/配额告警横幅；坐席工作台输入区接入快捷回复模板一键选用。当前成员邀请无入口、首页告警字段未渲染、快捷回复未对接。

**Why this priority**: PRD 3.1 成员邀请（多坐席协作无法配置）、3.8 首页健康度总览（管理员看不到配置未完成/系统降级）、3.8 快捷回复（坐席效率工具）均缺失。

**Independent Test**: 可独立验证——admin 在成员管理页邀请坐席，返回邀请链接（超 seat_limit 报 409）；首页顶部出现告警横幅（当后端返回 degradation_alert/warning_banner 时）；坐席工作台输入区可展开快捷回复列表一键填入。

**Acceptance Scenarios**:

1. **Given** admin 在成员邀请页输入坐席手机号，**When** 点击邀请，**Then** 调用 POST /tenant/invite，返回邀请链接；若已达 seat_limit 则 409 报错提示。
2. **Given** 后端 /tenant/home 返回 degradation_alert=true 或 warning_banner 非空，**When** 管理员进入首页，**Then** 顶部渲染降级横幅或配额预警横幅。
3. **Given** agent 在工作台输入区，**When** 展开快捷回复，**Then** 调用 GET /session/quick-replies 加载模板列表，点击一项一键填入输入框。

---

### User Story 6 - 订单发货与流式响应修复 (Priority: P1)

后端补齐订单"发货"端点（PAID/PROCESSING -> SHIPPED，记录物流单号），前端订单列表增加发货操作；后端流式端点改为复用 Agent 能力（graph.invoke / agent_service），不再绕过 RAG/MCP/安全/计费。这是报告第六章指出的"前后端均缺或后端设计偏差"项。

**Why this priority**: PRD 3.6 订单履约状态机关键跃迁缺失使履约主流程不完整；3.10 流式响应绕过 Agent 使"裸 LLM"回答与产品定位矛盾。

**Independent Test**: 可独立验证——后端 POST /order/{id}/ship 端点存在且仅 PAID/PROCESSING 可发货，发货后状态为 SHIPPED 并记录物流单号；前端订单列表对可发货订单显示发货按钮；流式端点返回的内容经过 RAG/工具（可通过契约测试验证其调用 agent_service 而非直接 litellm）。

**Acceptance Scenarios**:

1. **Given** 一笔 PAID 状态订单，**When** 坐席在订单列表点击"发货"并填入物流单号，**Then** 调用 POST /order/{id}/ship，订单状态转为 SHIPPED，记录物流单号。
2. **Given** 一笔 SHIPPED 状态订单，**When** 查看订单列表，**Then** 不显示"发货"按钮（状态机不允许再次发货）。
3. **Given** 前端发起流式消息请求 POST /session/message/stream，**When** 后端处理，**Then** 响应内容来自 Agent 主链路（经路由/检索/工具/安全/计费），而非裸 LLM completion。

---

### Edge Cases

- 验证码错误或过期时，注册/验证码登录返回 401，前端给出明确提示而非崩溃。
- 微信扫码回调 state 校验失败（伪造）时返回 400，前端提示授权失败。
- 支付端点对已支付账单再次调用返回 400，前端不重复跳转。
- 退回 Agent 时会话非坐席处理中状态返回 400，前端提示。
- CSAT 重复提交返回 409，前端提示已评价。
- 留言建单时若有在线坐席返回 409，前端提示"已有坐席在线，请等待认领"。
- 发货时订单状态非 PAID/PROCESSING 返回 400/409，前端提示状态不允许发货。
- 发货物流单号为空或格式非法时返回 422。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 登录页 MUST 以选项卡形式并列展示"账号密码 / 手机验证码 / 微信扫码"三种登录方式（PRD 3.1.2）。
- **FR-002**: 手机验证码登录 MUST 先调 /auth/sms/send 获取验证码，再调 /auth/login/sms 完成登录（PRD 3.1.2）。
- **FR-003**: 手机号注册 MUST 校验短信验证码——前端获取验证码并填入，后端 /auth/register MUST 接收并校验验证码后才创建租户（PRD 3.1.1，修复报告 6.3）。
- **FR-004**: 系统 MUST 提供邮箱注册入口（/auth/register/email）与邮箱登录入口（/auth/login/email）（PRD 3.1.1）。
- **FR-005**: 账单列表对 unpaid 账单 MUST 显示"支付"按钮，点击调用 /billing/pay/{id} 获取跳转 URL 并跳转（PRD 3.10）。
- **FR-006**: 坐席工作台认领会话后 MUST 展示 Agent 执行轨迹时间线（调 /session/{id}/trace）与建议方案（PRD 3.7）。
- **FR-007**: 坐席工作台认领后 MUST 提供"退回给 AI"按钮，调用 /session/{id}/return-to-agent 使会话回到 agent_handling（PRD 3.7）。
- **FR-008**: 会话 CLOSED 后 MUST 向用户弹出 CSAT 评价（1-5 星 + 可选文字），提交调用 /session/{id}/csat（PRD 3.7）。
- **FR-009**: 会话 waiting_human 且无在线坐席时 MUST 提供留言入口，提交调用 /session/{id}/leave-message 自动建单（PRD 3.7）。
- **FR-010**: 管理员 MUST 能邀请坐席成员（/tenant/invite），受 seat_limit 约束，超限 409（PRD 3.1）。
- **FR-011**: 首页 MUST 渲染 degradation_alert（检索降级横幅）与 warning_banner（配额预警横幅）（PRD 3.8）。
- **FR-012**: 坐席工作台输入区 MUST 接入快捷回复模板（/session/quick-replies），支持一键填入（PRD 3.8）。
- **FR-013**: 后端 MUST 新增订单发货端点 POST /order/{id}/ship，仅 PAID/PROCESSING 可发货，发货后状态 SHIPPED 并记录物流单号（PRD 3.6，修复报告 6.1）。
- **FR-014**: 前端订单列表 MUST 对可发货订单显示发货操作并调用发货端点（PRD 3.6）。
- **FR-015**: 后端流式端点 /session/message/stream MUST 复用 Agent 主链路（agent_service / graph.invoke），经过路由/检索/工具/安全/计费，不再直接调 litellm.completion（修复报告 6.2）。

### Key Entities *(include if feature involves data)*

- **Order（订单）**: 状态机新增 SHIPPED 跃迁；新增 tracking_no（物流单号）字段与 ship 操作。
- **CSAT（满意度评价）**: 会话维度 1-5 分 + 可选文字，一会话一条。
- **QuickReply（快捷回复）**: 租户隔离的模板内容 + 可选快捷指令。
- **RegisterRequest（注册请求）**: 新增 code（短信验证码）字段，后端校验。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: PRD 覆盖审查报告中第一批（P0）9 项 + 报告第六章 3 项后端偏差，全部端到端可演示走通。
- **SC-002**: 后端 49 个端点中前端覆盖数从约 25 提升至覆盖所有 P0 业务端点（auth 9 + tenant 7 + session 6 + billing 1 + order 1 中的 P0 项）。
- **SC-003**: 登录页三种方式任意一种均可完成登录进入后台，成功率 100%（正常凭证下）。
- **SC-004**: 坐席工作台从"只能认领"升级为"认领 + 看Trace + 退回AI"完整双向协作，且新会话到达可被坐席感知。
- **SC-005**: 订单履约状态机 PAID->SHIPPED->COMPLETED 可由坐席主动触发发货，闭环完整。
- **SC-006**: 流式响应内容与主链路 /message 一致（同经 Agent），不再出现"裸 LLM"回答。
- **SC-007**: 后端全量测试与前端全量测试在新增改动后保持通过（回归零破坏），TDD 新增测试全部通过。

## Assumptions

- 后端各 P0 端点（除发货、流式、注册验证码外）已实现且契约稳定，本 feature 以前端对接为主，不重写后端业务逻辑。
- demo 环境短信验证码由 /auth/sms/send 返回（demo 模式回传 code），前端可直接用于联调测试。
- 微信扫码登录在 demo 环境可走授权 URL 流程，真实扫码依赖微信开放平台配置，前端先打通"获取授权 URL + 回调处理"链路。
- 支付宝沙箱支付 demo 模式可由后端 mock 返回跳转 URL，前端处理跳转/展示 URL 即可。
- WebSocket 实时推送（报告 4.4）属于体验实时性提升，本 feature 优先用现有轮询保证功能可用，WS 替换列为 P1 后续（若时间允许在本 feature 内补）。
- TDD：后端用 pytest（SQLite in-memory + fakeredis + TestClient），前端用 vitest + @vue/test-utils；新增功能先写测试再实现。
- 宪法原则（分层边界、租户隔离、资金/状态防重防错、端到端验收、深度优先）贯穿实现，新增端点遵循 路由->service->crud 分层。


---

### User Story 7 - SSE 流式响应渲染 (Priority: P2)

用户发送消息后，前端通过 SSE 流式端点逐字渲染 Agent 回复，而非等待完整回复。当前 ChatWindow 仅用非流式 /message，用户需等待完整回复。

**Why this priority**: PRD 3.10/spec 010 业务流式输出，体验优化（"逐字输出"）。

**Independent Test**: 发送消息时调用 /session/message/stream，前端逐字 append chunk 到气泡，结束后显示完整回复。

**Acceptance Scenarios**:
1. **Given** 用户发送消息，**When** 前端调 sendMessageStream，**Then** 逐字渲染 chunk，最后 [DONE] 结束。
2. **Given** 流式返回 error 事件，**When** 前端解析，**Then** 显示错误提示。

---

### User Story 8 - WebSocket 实时推送替换轮询 (Priority: P2)

坐席工作台用 WebSocket /api/v1/ws/agent 替换 10s 轮询，新待认领会话实时到达。当前 AgentConsole 用 setInterval(loadWaiting, 10000) 轮询。

**Why this priority**: PRD 3.7 "WebSocket 实时到达，保证坐席能第一时间看到新的待处理会话"。

**Independent Test**: WS 连接后收到新会话事件，队列实时刷新；连接断开自动重连。

**Acceptance Scenarios**:
1. **Given** agent 登录工作台，**When** WS 连接 /api/v1/ws/agent?token=xxx，**Then** 连接成功，不再轮询。
2. **Given** 同租户有新转人工会话，**When** 服务端广播，**Then** 坐席队列实时新增该会话。
3. **Given** WS 连接断开，**When** 自动重连，**Then** 恢复实时推送。
