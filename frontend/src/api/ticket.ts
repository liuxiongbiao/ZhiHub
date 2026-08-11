// frontend/src/api/ticket.ts
// 职责: 工单列表/详情接口
// 分工: 🟢 弱模型可独立完成
// 对应日程: Day 5

import request from './request'

/** 工单项 */
export interface Ticket {
  id: number
  ticket_no: string
  title: string
  description?: string
  status: 'open' | 'in_progress' | 'resolved' | 'closed'
  priority: 'low' | 'medium' | 'high' | 'urgent'
  created_at: string
}

/** 获取工单列表（支持状态筛选+分页） */
export function getTicketList(params: { page?: number; page_size?: number; status?: string }) {
  return request.get<{ items: Ticket[]; total: number; page: number; page_size: number }>(
    '/ticket/list',
    { params }
  )
}

/** 获取工单详情 */
export function getTicketDetail(ticketId: number) {
  return request.get<Ticket>(`/ticket/${ticketId}`)
}
