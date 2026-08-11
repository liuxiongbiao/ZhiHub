# Agent 评测报告（009-tech-compare-agent-enhance）

## 意图识别准确率: 100.0%（10/10）
## 工具调用成功率: 100.0%（10/10）

## 详细
| # | 消息 | 预期意图 | 实际意图 | 预期工具 | 实际工具 | 意图✓ | 工具✓ |
|---|---|---|---|---|---|---|---|
| 1 | 如何升级套餐？ | knowledge_consult | knowledge_consult | - | - | ✓ | ✓ |
| 2 | 工单TK-20240001处理得怎么样了 | ticket_query | ticket_query | get_ticket_status | get_ticket_status | ✓ | ✓ |
| 3 | 订单ORD-20240001发货了吗 | order_query | order_query | query_order | query_order | ✓ | ✓ |
| 4 | 我要退款！你们服务太差了 | sensitive_action | sensitive_action | - | - | ✓ | ✓ |
| 5 | 怎么开具增值税发票 | knowledge_consult | knowledge_consult | - | - | ✓ | ✓ |
| 6 | 查工单TK-20240001，手机号13 | ticket_query | ticket_query | get_ticket_status | get_ticket_status | ✓ | ✓ |
| 7 | ORD-20240002的金额是多少 | order_query | order_query | query_order | query_order | ✓ | ✓ |
| 8 | 套餐太贵了能不能降级 | knowledge_consult | knowledge_consult | - | - | ✓ | ✓ |
| 9 | 登录页面一直转圈打不开 | knowledge_consult | knowledge_consult | - | - | ✓ | ✓ |
| 10 | 帮我投诉产品质量问题 | sensitive_action | sensitive_action | - | - | ✓ | ✓ |
