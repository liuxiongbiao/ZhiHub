# Tasks: PRD 覆盖缺口闭环（前后端联调补齐）

**Input**: Design documents from `/specs/013-prd-coverage-gap-closure/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/endpoints.md

**Tests**: 本 feature 采用 TDD--每个 user story 先写测试（红）再实现（绿）。

**Organization**: 按 user story 分阶段，每个 story 可独立实现与测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件，无依赖）
- **[Story]**: 所属 user story

## Path Conventions

- 后端: `zhihub/backend/app/...` + `zhihub/backend/tests/...`
- 前端: `zhihub/frontend/src/...` + `zhihub/frontend/tests/...`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 类型定义与枚举对齐（所有 user story 的前置）

- [X] T001 [P] 在 zhihub/frontend/src/types/auth.ts 补充 SmsLoginRequest/EmailRegisterRequest/EmailLoginRequest/WechatAuthResponse/WechatCallbackResponse 类型
- [X] T002 [P] 在 zhihub/frontend/src/types/session.ts 补充 TraceResponse/LeaveMessageResponse/CSATResponse/QuickReplyItem 类型
- [X] T003 [P] 在 zhihub/frontend/src/api/order.ts 将 Order.status 改为字面量联合类型并补 STATUS_MAP 中文映射；RefundApproval.status 补全枚举与映射

---

## Phase 2: Foundational (Backend 偏差修复 -- 跨 story 前置)

**Purpose**: 修复报告第六章 3 处后端偏差，前端对接依赖这些契约

- [X] T004 [TDD] 编写 zhihub/backend/tests/contracts/test_register_sms.py：正确验证码注册 201、错误验证码 401、缺 code 422
- [X] T005 在 zhihub/backend/app/schemas/auth.py 的 RegisterRequest 新增 code 必填字段
- [X] T006 在 zhihub/backend/app/services/auth_service.py 的 register() 增加验证码校验（复用 Redis sms_code:{phone}），无效抛 InvalidCode
- [X] T007 在 zhihub/backend/app/api/v1/auth.py 的 register 端点捕获 InvalidCode -> 401
- [X] T008 [TDD] 编写 zhihub/backend/tests/contracts/test_order_ship.py：paid 发货 200、shipped 发货 400、不存在 404、空 tracking_no 422
- [X] T009 在 zhihub/backend/app/models/order.py 的 Order 新增 tracking_no 字段（String(100), nullable）
- [X] T010 在 zhihub/backend/app/schemas/order.py 新增 ShipRequest{tracking_no}/ShipResponse
- [X] T011 在 zhihub/backend/app/services/order_service.py 新增 ship_order()（状态机校验 paid->shipped + 乐观锁 + 写 tracking_no + 审计日志）
- [X] T012 在 zhihub/backend/app/api/v1/order.py 新增 POST /{order_id}/ship 端点
- [X] T013 [TDD] 编写 zhihub/backend/tests/contracts/test_streaming_agent.py：断言流式端点调用 agent_service.handle_message 且 content 来自其返回，不再直接调 litellm
- [X] T014 修改 zhihub/backend/app/api/v1/session.py 的 /message/stream：改为先调 agent_service.handle_message 拿 Agent 回复，再 SSE 逐字 yield content
- [X] T015 运行 `cd zhihub/backend && python -m pytest tests/contracts/test_register_sms.py tests/contracts/test_order_ship.py tests/contracts/test_streaming_agent.py tests/unit/test_order_state_machine.py -q` 确认全绿

---

## Phase 3: US1 多方式登录与注册短信验证 (Priority: P1)

**Story Goal**: 登录页三选项卡 + 验证码/邮箱/微信登录；注册短信验证。独立测试: 登录页三方式可选，注册需验证码。

- [X] T016 [TDD] [US1] 编写 zhihub/frontend/tests/unit/auth-api.spec.ts：smsSend/loginSms/registerEmail/loginEmail/wechatAuth/wechatCallback 调用契约与路径断言
- [X] T017 [US1] 在 zhihub/frontend/src/api/auth.ts 新增 smsSend/loginSms/registerEmail/loginEmail/wechatAuth/wechatCallback；register 改为携带 code
- [X] T018 [TDD] [US1] 编写 zhihub/frontend/tests/components/Login.spec.ts：渲染三个选项卡；切验证码登录调 smsSend+loginSms；切微信调 wechatAuth
- [X] T019 [US1] 改造 zhihub/frontend/src/views/login/Login.vue：el-tabs 三选项卡（账号密码/手机验证码/微信扫码），验证码登录流程，微信扫码获取授权 URL
- [X] T020 [TDD] [US1] 扩展 zhihub/frontend/tests/components/Register.spec.ts：含验证码输入步骤，缺验证码不提交
- [X] T021 [US1] 改造 zhihub/frontend/src/views/login/Register.vue：增加"获取验证码"按钮（调 smsSend）+ 验证码输入框，register 携带 code；增加邮箱注册入口

---

## Phase 4: US2 支付入口闭环 (Priority: P1)

**Story Goal**: 账单列表 unpaid 账单显示支付按钮并跳转。独立测试: admin 对 unpaid 账单点支付拿到 URL。

- [X] T022 [TDD] [US2] 编写 zhihub/frontend/tests/unit/billing-api.spec.ts：payInvoice 调 POST /billing/pay/{id}
- [X] T023 [US2] 在 zhihub/frontend/src/api/billing.ts 新增 payInvoice(invoiceId)
- [X] T024 [TDD] [US2] 编写 zhihub/frontend/tests/components/InvoiceList.spec.ts：unpaid 显示支付按钮，paid 不显示；点击调 payInvoice
- [X] T025 [US2] 改造 zhihub/frontend/src/views/billing/InvoiceList.vue：unpaid 行增"支付"按钮，点击调 payInvoice 拿 pay_url 跳转（window.location 或展示 URL）

---

## Phase 5: US3 坐席工作台双向协作与 Trace (Priority: P1)

**Story Goal**: 认领后显示 Trace + 退回 AI 按钮。独立测试: 认领后右侧 Trace，点退回回到 agent_handling。

- [X] T026 [TDD] [US3] 扩展 zhihub/frontend/tests/unit/session-api.spec.ts：getTrace/returnToAgent 调用契约
- [X] T027 [US3] 在 zhihub/frontend/src/api/session.ts 补 getTrace（已定义 returnToAgent，补 getTrace）
- [X] T028 [US3] 改造 zhihub/frontend/src/views/session/AgentConsole.vue：认领后右侧接入 TraceTimeline 组件（调 getTrace）；操作区增"退回给 AI"按钮（调 returnToAgent），成功后刷新列表

---

## Phase 6: US4 CSAT 满意度评价与留言建单 (Priority: P1)

**Story Goal**: closed 弹 CSAT；waiting_human 无坐席显示留言。独立测试: closed 弹评价提交成功；留言返回工单号。

- [X] T029 [TDD] [US4] 扩展 zhihub/frontend/tests/unit/session-api.spec.ts：submitCsat/leaveMessage 调用契约
- [X] T030 [US4] 在 zhihub/frontend/src/api/session.ts 新增 submitCsat/leaveMessage
- [X] T031 [US4] 新建 zhihub/frontend/src/components/CsatDialog.vue（el-dialog + el-rate 1-5 + 可选文字）
- [X] T032 [US4] 新建 zhihub/frontend/src/components/LeaveMessageDialog.vue（el-dialog + textarea）
- [X] T033 [US4] 改造 zhihub/frontend/src/views/session/ChatWindow.vue：会话 closed 时弹 CsatDialog（提交调 submitCsat，409 提示已评价）；waiting_human 时弹 LeaveMessageDialog（提交调 leaveMessage，409 提示有坐席在线）

---

## Phase 7: US5 成员邀请、首页告警、快捷回复 (Priority: P1)

**Story Goal**: 邀请坐席；首页告警横幅；快捷回复。独立测试: 邀请返回链接超限409；首页横幅；快捷回复一键填入。

- [X] T034 [TDD] [US5] 编写 zhihub/frontend/tests/unit/tenant-api.spec.ts：inviteMember 调用契约
- [X] T035 [US5] 在 zhihub/frontend/src/api/tenant.ts 新增 inviteMember/getAuditLogs/updateWidgetConfig/getWidgetEmbedCode
- [X] T036 [US5] 新建 zhihub/frontend/src/views/onboarding/MemberInvite.vue（输入手机号调 inviteMember，409 提示超 seat_limit）；router 注册路由
- [X] T037 [US5] 改造 zhihub/frontend/src/views/dashboard/Home.vue：读取 tenantStore.home 的 degradation_alert/warning_banner 渲染顶部横幅（el-alert）
- [X] T038 [TDD] [US5] 扩展 zhihub/frontend/tests/unit/session-api.spec.ts：getQuickReplies 调用契约
- [X] T039 [US5] 在 zhihub/frontend/src/api/session.ts 新增 getQuickReplies/createQuickReply
- [X] T040 [US5] 改造 zhihub/frontend/src/views/session/AgentConsole.vue：输入区增快捷回复按钮（el-popover + 列表，调 getQuickReplies，点击一键填入输入框）

---

## Phase 8: US6 订单发货前端 + 流式对接 (Priority: P1)

**Story Goal**: 订单列表发货操作；流式对接（可选）。独立测试: paid 订单点发货填单号->shipped。

- [X] T041 [TDD] [US6] 编写 zhihub/frontend/tests/unit/order-api.spec.ts：shipOrder 调 POST /order/{id}/ship
- [X] T042 [US6] 在 zhihub/frontend/src/api/order.ts 新增 shipOrder/cancelRefund
- [X] T043 [US6] 改造 zhihub/frontend/src/views/order/OrderList.vue：paid 订单行增"发货"按钮，弹窗输入物流单号，调 shipOrder，成功刷新列表；shipped 不显示
- [X] T044 [P] [US6] 在 zhihub/frontend/src/api/session.ts 新增 sendMessageStream（fetch + SSE 解析 /session/message/stream）--可选增强
- [X] T045 [P] [US6] 改造 zhihub/frontend/src/views/session/ChatWindow.vue 接入流式（可选，若时间允许）

---

## Phase 9: Polish & 回归验证

- [X] T046 运行后端全量测试 `cd zhihub/backend && python -m pytest tests/ -q`，确认零回归
- [X] T047 运行前端全量测试 `cd zhihub/frontend && npx vitest run`，确认零回归
- [X] T048 [P] 更新 specs/013-prd-coverage-gap-closure/checklists/test.md 勾选验收项
- [X] T049 [P] 在 zhihub/docs 下补充本 feature 联调说明（端到端验收场景对应端点）


---

## Phase 10: US7 SSE 流式渲染 + US8 WebSocket 实时推送 (Priority: P2)

- [X] T050 [TDD] [US7] 编写 zhihub/frontend/tests/unit/session-api.spec.ts 扩展：sendMessageStream 解析 SSE chunk + [DONE]
- [X] T051 [US7] 在 zhihub/frontend/src/api/session.ts 新增 sendMessageStream(sessionId, content)：fetch + ReadableStream 解析 SSE
- [X] T052 [TDD] [US7] 编写 zhihub/frontend/tests/components/ChatStream.spec.ts：流式发送逐字渲染 chunk
- [X] T053 [US7] 改造 zhihub/frontend/src/components/chat/ChatWindow.vue：发送时调 sendMessageStream，增量 append chunk
- [X] T054 [TDD] [US8] 编写 zhihub/frontend/tests/unit/ws-client.spec.ts：mock WebSocket 连接/消息分发/重连
- [X] T055 [US8] 新建 zhihub/frontend/src/utils/ws-client.ts：WS 客户端（connect(token)/onMessage/reconnect/close）
- [X] T056 [US8] 改造 zhihub/frontend/src/views/agent/AgentConsole.vue：移除 setInterval 轮询，改用 ws-client 实时刷新队列
