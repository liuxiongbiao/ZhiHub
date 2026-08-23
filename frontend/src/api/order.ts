// frontend/src/api/order.ts
// 职责: 订单列表/详情/退款审批/发货/撤销 接口

import request from './request'

/** 订单状态枚举（对齐后端 models/order.py VALID_TRANSITIONS） */
export type OrderStatus =
  | 'created' | 'paid' | 'shipped' | 'completed'
  | 'cancelled' | 'refund_requested' | 'refunding' | 'refunded'

/** 退款审批状态枚举 */
export type RefundStatus =
  | 'pending' | 'approved' | 'rejected'
  | 'refunding' | 'refunded' | 'cancelled'

/** 订单状态中文映射 */
export const ORDER_STATUS_MAP: Record<OrderStatus, string> = {
  created: '待支付', paid: '已支付', shipped: '已发货', completed: '已完成',
  cancelled: '已取消', refund_requested: '待退款审批', refunding: '退款中', refunded: '已退款',
}

/** 退款状态中文映射 */
export const REFUND_STATUS_MAP: Record<RefundStatus, string> = {
  pending: '待审批', approved: '已批准', rejected: '已拒绝',
  refunding: '退款中', refunded: '已退款', cancelled: '已撤销',
}

/** 订单项 */
export interface Order {
  id: number
  order_no: string
  amount: number
  paid_amount: number
  status: OrderStatus
  tracking_no?: string | null
  created_by?: string
  version?: number
  created_at: string
}

/** 退款审批项 */
export interface RefundApproval {
  id: number
  order_id: number
  amount: number
  reason?: string
  status: RefundStatus
  created_at: string
}

export interface RefundRules {
  approval_threshold: number
  currency: 'CNY'
  over_limit_action: 'admin_review'
}

export interface RefundDecisionResponse {
  id: number
  status: RefundStatus
  message?: string
  requires_admin_review?: boolean
  already_processed?: boolean
}

/** 发货响应（规格 013 US6） */
export interface ShipResponse {
  id: number
  order_no: string
  status: 'shipped'
  tracking_no: string
}

/** 获取订单列表 */
export function getOrderList(params: { page?: number; page_size?: number }) {
  return request.get<{ items: Order[]; total: number; page: number; page_size: number }>(
    '/order/list', { params }
  )
}

/** 获取订单详情 */
export function getOrderDetail(orderId: number) {
  return request.get<Order>(`/order/${orderId}`)
}

/** 获取退款审批列表 */
export function getRefundList(params: { page?: number; page_size?: number }) {
  return request.get<{ items: RefundApproval[]; total: number; page: number; page_size: number }>(
    '/order/refunds/list', { params }
  )
}

/** 审批退款（approve/reject） */
export function getRefundRules() {
  return request.get<RefundRules>('/order/refunds/rules')
}

export function approveRefund(approvalId: number, decision: 'approved' | 'rejected') {
  return request.post<RefundDecisionResponse>(`/order/refunds/${approvalId}/approve`, { decision })
}

/** 撤销售后申请（PRD §3.6，仅 pending 可撤销，必须填写原因） */
export function cancelRefund(approvalId: number, reason: string) {
  return request.post(`/order/refunds/${approvalId}/cancel`, { reason })
}

/** 订单发货（规格 013 US6，paid → shipped） */
export function shipOrder(orderId: number, tracking_no: string) {
  return request.post<ShipResponse>(`/order/${orderId}/ship`, { tracking_no })
}

/** 创建订单（PRD §§3.6，admin/agent） */
export interface CreateOrderBody {
  order_no: string
  amount: number
  paid_amount?: number
  phone?: string
}

export function createOrder(body: CreateOrderBody) {
  return request.post<Order>('/order', body)
}
