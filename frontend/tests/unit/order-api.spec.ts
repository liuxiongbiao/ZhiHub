/**
 * tests/unit/order-api.spec.ts
 * 013 US6 (T041): shipOrder 调 POST /order/{id}/ship；cancelRefund 调 POST /order/refunds/{id}/cancel
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
const { mockRequest } = vi.hoisted(() => ({
  mockRequest: { get: vi.fn(), post: vi.fn() }
}))
vi.mock('@/api/request', () => ({ default: mockRequest }))
import { shipOrder, cancelRefund, ORDER_STATUS_MAP } from '@/api/order'
beforeEach(() => { vi.clearAllMocks() })
describe('order api (013 US6)', () => {
  it('shipOrder posts to /order/{id}/ship with tracking_no', async () => {
    mockRequest.post.mockResolvedValue({ data: { id: 1, status: 'shipped' } })
    await shipOrder(5, 'SF123')
    expect(mockRequest.post).toHaveBeenCalledWith('/order/5/ship', { tracking_no: 'SF123' })
  })
  it('cancelRefund posts to /order/refunds/{id}/cancel', async () => {
    mockRequest.post.mockResolvedValue({ data: { id: 3, status: 'cancelled' } })
    await cancelRefund(3)
    expect(mockRequest.post).toHaveBeenCalledWith('/order/refunds/3/cancel')
  })
  it('ORDER_STATUS_MAP has shipped -> 已发货', () => {
    expect(ORDER_STATUS_MAP['shipped']).toBe('已发货')
  })
})
