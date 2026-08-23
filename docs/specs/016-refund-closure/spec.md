# 016 退款闭环（refund-closure）

> 状态：实施中 | 日期：2026-08-12
> 关联：修复清单 P0-4 / P0-5

## 1. 背景与目标

补齐 PRD 3.5/3.6 退款闭环：
1. 已发货订单（shipped）也可以发起退款。
2. 存在“坐席代终端用户创建退款申请”的接口（此前只有 admin 直调 MCP，且无身份校验）。
3. 退款申请必须携带手机号做双因子校验（与订单绑定手机号后 4 位一致）。
4. 终端用户可在 Widget 会话内撤销自己的 pending 退款申请（依赖 014 Widget 通道）。

## 2. 业务规则

- 状态机：`paid/shipped -> refund_requested -> refunding -> refunded`；拒绝或撤销从
  `refund_requested` 精确恢复到申请前的 `paid/shipped`。
- 双因子：创建退款申请时 `phone` 后 4 位必须与 `orders.phone` 一致；不一致统一报“身份校验未通过”。
- 幂等：`client_request_id + order_id` 维度去重（既有机制）。
- 金额：`0 < amount <= paid_amount`（既有机制）。
- 撤销：仅 `pending` 审批可撤销；Widget 撤销需提供会话 + 手机号（后 4 位匹配）。

## 3. 验收条件

- [ ] `shipped` 订单调用 `request_refund` 成功进入 `refund_requested`。
- [ ] `POST /api/v1/order/refunds/request`（admin/agent）：phone 匹配 → 201 创建申请；不匹配 → 400。
- [ ] MCP `request_refund` 缺 phone → -32602；phone 不匹配 → 业务拒绝。
- [ ] 同一 `client_request_id` 重复请求只创建一条申请。
- [ ] Widget 撤销接口：phone 匹配且 pending → 撤销成功；不匹配 → 403/400。

## 4. 边界与错误处理

- 终态订单（refunded/completed/cancelled）不可发起退款。
- 撤销已审批的申请 → 400。
- 订单不存在/身份不匹配 → 统一“未查询到匹配信息”风格，不泄露存在性。

## 5. 非功能约束

- 资金操作保持乐观锁 CAS（version 字段），禁止覆盖写。
- 所有金额用 Decimal/Numeric(10,2)。
