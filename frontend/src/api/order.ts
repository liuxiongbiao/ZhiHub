// frontend/src/api/order.ts
// 职责: 订单列表/详情/退款审批/发货/撤销 接口

import request from './request'

/** 订单状态枚举（对齐后端 models/order.py VALID_TRANSITIONS） */
export type OrderStatus =
  | 'created' | 'paid' | 'shipped' | 'completed'
  | 'cancelled' | 'refunding' | 'refunded'

/** 退款审批状态枚举 */
export type RefundStatus =
  | 'pending' | 'approved' | 'rejected'
  | 'refunding' | 'refunded' | 'cancelled'

/** 订单状态中文映射 */
export const ORDER_STATUS_MAP: Record<OrderStatus, string> = {
  created: '待支付', paid: '已支付', shipped: '已发货', completed: '已完成',
  cancelled: '已取消', refunding: '退款中', refunded: '已退款',
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

/** 发货响应（013 US6） */
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
export function approveRefund(approvalId: number, decision: 'approved' | 'rejected') {
  return request.post(`/order/refunds/${approvalId}/approve`, { decision })
}

/** 撤销售后申请（013，仅 pending 可撤销） */
export function cancelRefund(approvalId: number) {
  return request.post(`/order/refunds/${approvalId}/cancel`)
}

/** 订单发货（013 US6，paid -> shipped） */
export function shipOrder(orderId: number, tracking_no: string) {
  return request.post<ShipResponse>(`/order/${orderId}/ship`, { tracking_no })
}

/** 创建订单（closure-plan P1-3，PRD 3.6，admin/agent） */
export interface CreateOrderBody {
  order_no: string
  amount: number
  paid_amount?: number
  phone?: string
}

export function createOrder(body: CreateOrderBody) {
  return request.post<Order>('/order', body)
}
