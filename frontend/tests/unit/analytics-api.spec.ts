import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockRequest } = vi.hoisted(() => ({ mockRequest: { get: vi.fn() } }))
vi.mock('@/api/request', () => ({ default: mockRequest }))

import { getAnalyticsOverview } from '@/api/analytics'

beforeEach(() => vi.clearAllMocks())

describe('analytics api', () => {
  it('requests the tenant overview with selected period', async () => {
    mockRequest.get.mockResolvedValue({ data: {} })
    await getAnalyticsOverview(90)
    expect(mockRequest.get).toHaveBeenCalledWith('/analytics/overview', { params: { days: 90 } })
  })
})
