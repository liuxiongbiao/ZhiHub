/**
 * tests/unit/refresh-token-batch5.spec.ts
 * 职责: 验证 401 并发时 single-flight 刷新、原请求重放与失败登出。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AUTH_STORAGE_KEY } from '@/constants'

const handlers: {
  request?: (config: any) => any
  fulfilled?: (response: any) => any
  rejected?: (error: any) => Promise<any>
} = {}

const replay = vi.fn(async (config: any) => ({ data: { replayed: config.url } })) as any
replay.interceptors = {
  request: { use: vi.fn((fn: any) => { handlers.request = fn }) },
  response: {
    use: vi.fn((ok: any, fail: any) => {
      handlers.fulfilled = ok
      handlers.rejected = fail
    }),
  },
}
replay.defaults = { baseURL: '/api/v1' }

const refreshPost = vi.fn()

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => replay),
    post: refreshPost,
  },
}))

describe('refresh token single-flight', () => {
  beforeEach(async () => {
    vi.resetModules()
    vi.clearAllMocks()
    localStorage.clear()
    handlers.rejected = undefined
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({
      token: 'expired-access',
      refresh_token: 'refresh-1',
      user_id: 7,
      tenant_id: 3,
      role: 'admin',
    }))
    await import('@/api/request')
  })

  it('coalesces concurrent 401 responses into one refresh and replays both', async () => {
    let resolveRefresh!: (value: any) => void
    refreshPost.mockReturnValue(new Promise((resolve) => { resolveRefresh = resolve }))
    const unauthorized = handlers.rejected!
    const first = unauthorized({ response: { status: 401 }, config: { url: '/orders', headers: {} } })
    const second = unauthorized({ response: { status: 401 }, config: { url: '/tickets', headers: {} } })

    expect(refreshPost).toHaveBeenCalledTimes(1)
    resolveRefresh({ data: { access_token: 'access-2', refresh_token: 'refresh-2' } })

    await expect(Promise.all([first, second])).resolves.toEqual([
      { data: { replayed: '/orders' } },
      { data: { replayed: '/tickets' } },
    ])
    expect(replay).toHaveBeenCalledTimes(2)
    expect(JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY)!).refresh_token).toBe('refresh-2')
  })

  it('clears auth only when refresh fails', async () => {
    const onUnauthorized = vi.fn()
    const { setUnauthorizedHandler } = await import('@/api/request')
    setUnauthorizedHandler(onUnauthorized)
    refreshPost.mockRejectedValue(new Error('refresh expired'))

    await expect(handlers.rejected!({
      response: { status: 401, data: { detail: '未认证' } },
      config: { url: '/orders', headers: {} },
    })).rejects.toThrow()

    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
    expect(replay).not.toHaveBeenCalled()
  })
})
