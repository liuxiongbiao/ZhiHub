# 端到端自动解决率评测报告（阈值 0.3 验证）

> 执行时间: 2026-08-23 10:32:12 | 方法: tenant 1 逐题新会话调 handle_message | 检索阈值: 0.3

## 汇总

- 总题数: 18
- 自动回答(ANSWERED): 7
- 转人工(HANDOFF): 8
- 反问澄清(CLARIFY): 3
- 其他(EMPTY/ERROR): 0 / 0
- 自动解决率 = ANSWERED/总数 = **7/18 = 38.9%**
- 期望行为符合率 = **14/18 = 77.8%**
- 知识库外问题(应转人工): 5 题，实际转人工 5 题

## 逐题明细

| # | 类别 | 问题 | 意图 | 会话状态 | 分类 | 期望 | 符合 | 耗时ms | 置信度 | 回复内容 |
|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | 精确匹配类 | 工单编号TK-20240001的当前状态是什么？ | ticket_query | agent_handling | CLARIFY | CLARIFY | ✅ | 4667 | 0.95 | 为保护您的账户安全，查询工单需验证身份。请提供您在工单中绑定的手机号（11位）。 |
| Q2 | 精确匹配类 | 订单ORD-20240002的金额是多少？ | order_query | agent_handling | CLARIFY | CLARIFY | ✅ | 3189 | 0.9 | 为保护您的账户安全，查询订单需验证身份。请提供您在订单中绑定的手机号后四位（或完整 11 位手机号）。 |
| Q3 | 精确匹配类 | 退款政策是什么？ | knowledge_consult | agent_handling | ANSWERED | ANSWERED | ✅ | 9778 | 0.95 | 根据现有文档，退款政策主要包括以下几点：

1. **适用范围**：已支付订单可申请退款，退款金额不超过订单实付金额。
2. **申请时机**：已支付订单在发货 |
| Q4 | 精确匹配类 | 如何开通增值税专用发票？ | knowledge_consult | agent_handling | ANSWERED | ANSWERED | ✅ | 7200 | 0.95 | 根据现有文档，开通增值税专用发票需要提供完整的开票信息，包括：**公司名称、税号、地址电话、开户行及账号**。信息不完整将无法开具专票。支付成功后可申请开具发票 |
| Q5 | 精确匹配类 | 套餐升级后多久生效？ | knowledge_consult | agent_handling | ANSWERED | ANSWERED | ✅ | 7100 | 0.95 | 根据检索到的文档信息，套餐升级后**立即生效**，无需等待下个计费周期。升级完成后，您可以马上享受新套餐的全部权益。

---
来源：套餐升级指南.md - 片 |
| Q6 | 语义匹配类 | 怎么把现在的套餐换成更高级的？ | knowledge_consult | agent_handling | ANSWERED | ANSWERED | ✅ | 6787 | 0.95 | 根据文档信息，您可以在后台账户设置中升级到更高级别的套餐。具体操作是：进入账户设置页面，选择套餐升级即可。升级后立即生效，无需等待下个计费周期，升级后可立即享受 |
| Q7 | 语义匹配类 | 付完钱之后多久能收到货？ | knowledge_consult | agent_handling | ANSWERED | ANSWERED | ✅ | 6704 | 0.93 | 根据现有信息，已支付订单通常在 **24 小时内发货**，但具体到货时间取决于物流配送时效，文档中未提供明确的预计送达天数。发货后，您可以在订单页面查看物流单号 |
| Q8 | 语义匹配类 | 我不想用这个产品了，钱能退吗？ | sensitive_action | waiting_human | HANDOFF | ANSWERED | ❌ | 2830 | 0.85 | 您的问题因以下原因已转接人工客服：涉及敏感操作（退款/投诉等）。
请稍候，我们的坐席将尽快为您服务。
如长时间无人响应，您的消息将自动创建工单。 |
| Q9 | 语义匹配类 | 登录的时候一直转圈进不去 | knowledge_consult | waiting_human | HANDOFF | ANSWERED | ❌ | 11219 | 0.85 | 当前问题未在知识库中找到足够可信的答案，已为您转接人工客服，请稍候。 |
| Q10 | 语义匹配类 | 有没有便宜点的方案？ | knowledge_consult | waiting_human | HANDOFF | ANSWERED | ❌ | 10309 | 0.9 | 当前问题未在知识库中找到足够可信的答案，已为您转接人工客服，请稍候。 |
| Q11 | 知识库外问题 | 今天天气怎么样？ | knowledge_consult | waiting_human | HANDOFF | HANDOFF | ✅ | 3132 | 0.35 | 您的问题因以下原因已转接人工客服：AI置信度不足 (35%)。
请稍候，我们的坐席将尽快为您服务。
如长时间无人响应，您的消息将自动创建工单。 |
| Q12 | 知识库外问题 | 推荐一家附近的餐厅 | knowledge_consult | waiting_human | HANDOFF | HANDOFF | ✅ | 10560 | 0.85 | 当前问题未在知识库中找到足够可信的答案，已为您转接人工客服，请稍候。 |
| Q13 | 知识库外问题 | Python的装饰器怎么用？ | knowledge_consult | waiting_human | HANDOFF | HANDOFF | ✅ | 9950 | 0.95 | 当前问题未在知识库中找到足够可信的答案，已为您转接人工客服，请稍候。 |
| Q14 | 知识库外问题 | 帮我写一封辞职信 | knowledge_consult | waiting_human | HANDOFF | HANDOFF | ✅ | 9582 | 0.85 | 当前问题未在知识库中找到足够可信的答案，已为您转接人工客服，请稍候。 |
| Q15 | 知识库外问题 | 2024年奥运会金牌榜 | knowledge_consult | waiting_human | HANDOFF | HANDOFF | ✅ | 10508 | 0.9 | 当前问题未在知识库中找到足够可信的答案，已为您转接人工客服，请稍候。 |
| Q16 | 混合查询 | 订单退款的政策和流程 | knowledge_consult | agent_handling | ANSWERED | ANSWERED | ✅ | 6750 | 0.9 | 根据现有文档，订单退款的政策和流程如下：

**退款政策：**
- 已支付订单可申请退款，退款金额不超过订单实付金额，退款将原路退回至用户支付时使用的账户。
- |
| Q17 | 混合查询 | 怎么查看我的工单处理到哪一步了 | ticket_query | agent_handling | CLARIFY | ANSWERED | ❌ | 2719 | 0.9 | 请提供您的工单编号（格式：TK-XXXXXXXX），以便我为您查询工单进度。 |
| Q18 | 混合查询 | 套餐太贵了能不能分期付款 | knowledge_consult | agent_handling | ANSWERED | ANSWERED | ✅ | 7821 | 0.85 | 根据现有信息，套餐费用较高时**支持分期付款**。分期方案可选择 **3期、6期或12期**，将套餐费用分期支付，以减轻一次性支付压力。

此外，您也可以考虑* |

## 备注

分类规则: HANDOFF=转人工 / ANSWERED=带引用回答 / CLARIFY=反问澄清 / EMPTY=无法回答 / ERROR=异常
