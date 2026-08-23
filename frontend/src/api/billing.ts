// frontend/src/api/billing.ts
// 职责: 配额/账单/用量接口

import request from './request'

/** 配额信息 */
export interface Quota {
  count: number
  quota: number
  remaining: number
}

/** 账单项 */
export interface Invoice {
  id: number
  period: string
  base_fee: number
  overage_fee: number
  status: 'unpaid' | 'paid'
  paid_at?: string
}

/** 获取当前配额 */
export function getQuota() {
  return request.get<Quota>('/billing/quota')
}

/** 获取账单列表 */
export function getInvoices() {
  return request.get<{ items: Invoice[] }>('/billing/invoices')
}

/** 获取用量概览 */
export function getUsage() {
  return request.get<{ quota: Quota }>('/billing/usage')
}


/** 支付响应（规格 013 US2） */
export interface PayResponse {
  trade_no?: string
  pay_url?: string
}

/** 发起支付（规格 013 US2，生成支付宝沙箱订单 + 跳转 URL） */
export function payInvoice(invoiceId: number) {
  return request.post<PayResponse>(`/billing/pay/${invoiceId}`)
}

/** 用量明细 CSV 导出（PRD §3.9，审查 ，仅 admin） */
export function exportUsage() {
  return request.get('/billing/usage/export', { responseType: 'blob' })
}

/** 用量与 Token 消耗明细 CSV 导出（PRD §3.9，会话ID/时间/意图/模型/Token/是否超额，仅 admin） */
export function exportUsageDetail() {
  return request.get('/billing/usage/export-detail', { responseType: 'blob' })
}
