/**
 * tests/unit/billing-api.spec.ts
 * 013 US2 (T022): payInvoice 调 POST /billing/pay/{id}
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
const { mockRequest } = vi.hoisted(() => ({
  mockRequest: { get: vi.fn(), post: vi.fn() }
}))
vi.mock('@/api/request', () => ({ default: mockRequest }))
import { payInvoice } from '@/api/billing'
beforeEach(() => { vi.clearAllMocks() })
describe('billing api (013 US2)', () => {
  it('payInvoice posts to /billing/pay/{id}', async () => {
    mockRequest.post.mockResolvedValue({ data: { pay_url: 'http://pay' } })
    await payInvoice(7)
    expect(mockRequest.post).toHaveBeenCalledWith('/billing/pay/7')
  })
})
