import { beforeEach, describe, expect, it, vi } from 'vitest'

const { request } = vi.hoisted(() => ({
  request: { get: vi.fn(), post: vi.fn() },
}))
vi.mock('@/api/request', () => ({ default: request }))

import { getLoginOptions, loginDemo } from '@/api/auth'
import { getDemoStatus, restoreDemo } from '@/api/demo'
import { getDeepTasks } from '@/api/agent'
import { getKnowledgeQuality } from '@/api/analytics'
import { getSessionBusinessContext } from '@/api/session'
import { transitionTicket } from '@/api/ticket'
import { getOrderList, requestRefund } from '@/api/order'

beforeEach(() => vi.clearAllMocks())

describe('productization API contracts', () => {
  it('loads public login capabilities and sends only a demo persona', async () => {
    request.get.mockResolvedValue({ data: {} })
    request.post.mockResolvedValue({ data: {} })
    await getLoginOptions()
    await loginDemo('agent1')
    expect(request.get).toHaveBeenCalledWith('/auth/login/options')
    expect(request.post).toHaveBeenCalledWith('/auth/login/demo', { persona: 'agent1' })
  })

  it('uses an explicit confirmation phrase for safe demo restore', async () => {
    request.get.mockResolvedValue({ data: {} })
    request.post.mockResolvedValue({ data: {} })
    await getDemoStatus()
    await restoreDemo()
    expect(request.get).toHaveBeenCalledWith('/demo/status')
    expect(request.post).toHaveBeenCalledWith('/demo/restore', { confirm: 'RESTORE_DEMO' })
  })

  it('loads the knowledge quality and recent DeepAgent task workspaces', async () => {
    request.get.mockResolvedValue({ data: {} })
    await getKnowledgeQuality(7)
    await getDeepTasks(8)
    expect(request.get).toHaveBeenNthCalledWith(1, '/analytics/knowledge-quality', { params: { days: 7 } })
    expect(request.get).toHaveBeenNthCalledWith(2, '/agent/deep-tasks', { params: { limit: 8 } })
  })

  it('loads session-scoped business context and advances a ticket', async () => {
    request.get.mockResolvedValue({ data: {} })
    request.post.mockResolvedValue({ data: {} })
    await getSessionBusinessContext(42)
    await transitionTicket(9, 'in_progress')
    expect(request.get).toHaveBeenCalledWith('/session/42/business-context')
    expect(request.post).toHaveBeenCalledWith('/ticket/9/transition', { status: 'in_progress' })
  })

  it('filters orders server-side and submits an idempotent verified refund', async () => {
    request.get.mockResolvedValue({ data: {} })
    request.post.mockResolvedValue({ data: {} })
    await getOrderList({ page: 2, status: 'paid' })
    const body = {
      order_no: 'ORD-1', phone: '2222', amount: 99,
      reason: '客户申请', client_request_id: 'refund-ui-1',
    }
    await requestRefund(body)
    expect(request.get).toHaveBeenCalledWith('/order/list', { params: { page: 2, status: 'paid' } })
    expect(request.post).toHaveBeenCalledWith('/order/refunds/request', body)
  })
})
