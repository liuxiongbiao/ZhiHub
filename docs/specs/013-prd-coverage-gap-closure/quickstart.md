# Quickstart: PRD 覆盖缺口闭环 验收指南

> 每个 user story 的端到端验收场景。后端测试: `cd zhihub/backend && python -m pytest tests/ -q`；前端测试: `cd zhihub/frontend && npx vitest run`。

## 前置
- 后端: SQLite in-memory + fakeredis（conftest 自动），无需真实 MySQL/Redis。
- 前端: vitest + jsdom，mock axios（request 模块）验证 api 调用契约。

## US1 多方式登录与注册短信验证
1. **后端契约测试** `tests/contracts/test_register_sms.py`: 正确验证码注册 201；错误验证码 401；缺 code 422。
2. **前端组件测试** `tests/components/Login.spec.ts`: 渲染三个选项卡；切换验证码登录调用 smsSend + loginSms。
3. **端到端**: 登录页切"手机验证码"->获取验证码->登录进后台；注册页填手机号->获取验证码->填入->注册成功。

## US2 支付入口闭环
1. **前端 api 测试** `tests/unit/billing-api.spec.ts`: payInvoice 调 POST /billing/pay/{id}。
2. **端到端**: admin 进账单列表，unpaid 账单显示"支付"按钮，点击拿到 pay_url 跳转；paid 账单无按钮。

## US3 坐席工作台双向协作与 Trace
1. **前端 api 测试** `tests/unit/session-api.spec.ts`: returnToAgent/getTrace 调用契约。
2. **端到端**: agent 认领会话->右侧显示 Trace 时间线->点"退回给 AI"->会话回到 agent_handling。

## US4 CSAT 与留言建单
1. **前端 api 测试** `tests/unit/session-api.spec.ts`: submitCsat/leaveMessage 调用契约。
2. **端到端**: closed 会话弹 CSAT 评价->提交成功；waiting_human 无坐席时显示留言入口->提交返回工单号。

## US5 成员邀请、首页告警、快捷回复
1. **前端 api 测试** `tests/unit/tenant-api.spec.ts`: inviteMember 调用契约；超 seat_limit 409。
2. **端到端**: admin 邀请坐席返回链接；首页顶部渲染降级/配额横幅；工作台输入区展开快捷回复一键填入。

## US6 订单发货与流式修复
1. **后端契约测试** `tests/contracts/test_order_ship.py`: paid 发货 200；shipped 发货 400；不存在 404；空 tracking_no 422。
2. **后端契约测试** `tests/contracts/test_streaming_agent.py`: 流式端点调用 agent_service.handle_message，content 来自其返回。
3. **后端单测** `tests/unit/test_order_state_machine.py`: SHIPPED 跃迁校验。
4. **端到端**: 订单列表 paid 订单点"发货"填物流单号->状态 shipped；流式请求返回 Agent 内容。

## US7 SSE 流式响应渲染
1. **前端 api 测试** `tests/unit/session-api.spec.ts`: sendMessageStream 解析 SSE chunk + [DONE]。
2. **前端组件测试** `tests/components/ChatStream.spec.ts`: 流式发送逐字渲染 chunk；失败回退错误提示。
3. **端到端**: ChatWindow 发送消息 -> 逐字渲染 Agent 回复（经 Agent 主链路），流式端点不可用时自动回退非流式 /message。

## US8 WebSocket 实时推送
1. **前端单元测试** `tests/unit/ws-client.spec.ts`: 连接/消息分发/指数退避重连/关闭。
2. **端到端**: agent 登录工作台 -> WS 连接 /api/v1/ws/agent?token=xxx（不再 10s 轮询）；同租户新转人工会话实时刷新队列；typing 事件显示"输入中"。
