/**
 * 批次 4 前端 API、公开路由与知识库权限契约测试。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { request } = vi.hoisted(() => ({
  request: { post: vi.fn(), get: vi.fn() },
}))
vi.mock('@/api/request', () => ({ default: request }))

import router from '@/router'
import { activateEmail } from '@/api/auth'
import { acceptInvite } from '@/api/tenant'

beforeEach(() => vi.clearAllMocks())

describe('batch4 public contracts', () => {
  it('activates email with the query token contract', async () => {
    request.post.mockResolvedValue({ data: { user_id: 1, email: 'a@example.com', activated: true } })
    await activateEmail('activation-token-1234')
    expect(request.post).toHaveBeenCalledWith('/auth/email/activate', { token: 'activation-token-1234' })
  })

  it('accepts an invite through the public endpoint', async () => {
    request.post.mockResolvedValue({ data: { user_id: 1, tenant_id: 2, role: 'agent' } })
    await acceptInvite({ token: 'invite-token' })
    expect(request.post).toHaveBeenCalledWith('/tenant/invite/accept', { token: 'invite-token' })
  })

  it('registers activation and invitation as public routes', () => {
    const activation = router.getRoutes().find((route) => route.path === '/activate-email')
    const invitation = router.getRoutes().find((route) => route.path === '/accept-invite')
    expect(activation?.meta.public).toBe(true)
    expect(invitation?.meta.public).toBe(true)
  })

  it('restricts knowledge pages to admins', () => {
    for (const path of ['/knowledge/list', '/knowledge/search-test']) {
      const route = router.getRoutes().find((item) => item.path === path)
      expect(route?.meta.requiresRole).toEqual(['admin'])
    }
  })
})
