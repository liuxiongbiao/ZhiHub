/** 各页面原来的文案。同一状态在不同页面用词不同，所以分开保留。 */

export function consoleIntentLabel(value?: string) {
  return ({
    knowledge_consult: '知识咨询',
    order_query: '订单问题',
    ticket_query: '工单问题',
    sensitive_action: '风险售后',
    round_handoff: '多轮未解决',
  } as Record<string, string>)[value || ''] || '待识别诉求'
}

export function consoleSessionStatus(value: string) {
  return ({
    waiting_human: '等待认领',
    human_handling: '人工处理中',
    agent_handling: 'AI 处理中',
    routing: '正在判断',
  } as Record<string, string>)[value] || value
}

export function consoleOrderStatus(value: string) {
  return ({
    created: '待支付',
    paid: '已支付',
    shipped: '已发货',
    completed: '已完成',
    refund_requested: '退款待审批',
    refunding: '退款中',
    refunded: '已退款',
  } as Record<string, string>)[value] || value
}

export function consoleTicketStatus(value: string) {
  return ({
    open: '待处理',
    in_progress: '处理中',
    resolved: '已解决',
    closed: '已关闭',
  } as Record<string, string>)[value] || value
}

export function traceIntentLabel(value: string) {
  return ({
    knowledge_consult: '知识咨询',
    order_query: '订单查询',
    ticket_query: '工单查询',
    sensitive_action: '风险事项',
    round_handoff: '多轮未解决',
  } as Record<string, string>)[value] || '客户咨询'
}

export function dashboardPlanLabel(value: string) {
  return ({ trial: '试用', basic: '基础', pro: '专业', enterprise: '企业' } as Record<string, string>)[value] || value
}

export function platformPlanLabel(value: string) {
  return ({ trial: '试用版', basic: '基础版', pro: '专业版', enterprise: '企业版' } as Record<string, string>)[value] || value
}

export const OPS_EVENT_LABELS: Record<string, string> = {
  session_created: '新建会话',
  retrieval_executed: '执行检索',
  mcp_tool_called: '调用工具',
  human_takeover: '人工接管',
  order_state_changed: '订单流转',
  refund_approved: '退款通过',
  refund_rejected: '退款驳回',
  quota_exceeded: '配额超限',
  invoice_paid: '账单支付',
  security_block: '安全拦截',
  answer_feedback: '回答反馈',
  index_rebuild_triggered: '重建索引',
  degradation_mode_entered: '进入降级',
  degradation_mode_exited: '退出降级',
  message_left: '用户留言',
  session_resolved: '会话解决',
}
