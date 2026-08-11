/**
 * tests/unit/tenant-api.spec.ts
 * 013 US5 (T034): inviteMember 调用契约
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
const { mockRequest } = vi.hoisted(() => ({
  mockRequest: { get: vi.fn(), post: vi.fn() }
}))
vi.mock('@/api/request', () => ({ default: mockRequest }))
import { inviteMember, getAuditLogs } from '@/api/tenant'
beforeEach(() => { vi.clearAllMocks() })
describe('tenant api (013 US5)', () => {
  it('inviteMember posts to /tenant/invite with phone', async () => {
    mockRequest.post.mockResolvedValue({ data: { invite_url: 'http://x' } })
    await inviteMember('13900139000')
    expect(mockRequest.post).toHaveBeenCalledWith('/tenant/invite', { phone: '13900139000' })
  })
  it('getAuditLogs gets /tenant/audit-logs', async () => {
    mockRequest.get.mockResolvedValue({ data: { items: [], total: 0 } })
    await getAuditLogs(1, 20)
    expect(mockRequest.get).toHaveBeenCalledWith('/tenant/audit-logs', { params: { page: 1, page_size: 20 } })
  })
})
