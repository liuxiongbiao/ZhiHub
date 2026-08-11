/**
 * tests/unit/user-store.spec.ts
 *
 * 测 src/store/modules/user.ts: login/register/logout actions、localStorage 水合、
 * isLoggedIn getter、reset。mock @/api/auth 的 login/register/logout。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
  loginSms: vi.fn(),
  loginEmail: vi.fn(),
  smsSend: vi.fn(),
  registerEmail: vi.fn(),
  wechatAuth: vi.fn(),
  wechatCallback: vi.fn(),
}))

const { login: apiLogin, register: apiRegister, logout: apiLogout } = await import('@/api/auth')
const { useUserStore } = await import('@/store/modules/user')
const { AUTH_STORAGE_KEY } = await import('@/constants')

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.clear()
})

describe('useUserStore', () => {
  it('hydrates state from localStorage on creation', () => {
    localStorage.setItem(
      AUTH_STORAGE_KEY,
      JSON.stringify({ token: 'stored-token', user_id: 42, tenant_id: 7, role: 'admin', phone: null }),
    )
    const store = useUserStore()
    expect(store.token).toBe('stored-token')
    expect(store.user_id).toBe(42)
    expect(store.tenant_id).toBe(7)
    expect(store.role).toBe('admin')
  })

  it('defaults to empty state when localStorage is empty', () => {
    const store = useUserStore()
    expect(store.token).toBe('')
    expect(store.isLoggedIn).toBe(false)
  })

  it('login action stores token / user_id / tenant_id / role', async () => {
    ;(apiLogin as any).mockResolvedValue({ data: { access_token: 'jwt-123', user_id: 1, tenant_id: 2, role: 'admin' } })
    const store = useUserStore()
    await store.login({ phone: '13800138000', password: 'abc12345' })

    expect(store.token).toBe('jwt-123')
    expect(store.user_id).toBe(1)
    expect(store.tenant_id).toBe(2)
    expect(store.role).toBe('admin')
    expect(store.isLoggedIn).toBe(true)
    // persisted
    const raw = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY)!)
    expect(raw.token).toBe('jwt-123')
  })

  it('login failure does not mutate state', async () => {
    ;(apiLogin as any).mockRejectedValue(new Error('401'))
    const store = useUserStore()
    await expect(store.login({ phone: '13800138000', password: 'wrong' })).rejects.toThrow()
    expect(store.token).toBe('')
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull()
  })

  it('register action calls api and returns response (does not set login state)', async () => {
    ;(apiRegister as any).mockResolvedValue({ data: { user_id: 5, tenant_id: 3, phone: '13900000000', role: 'admin', plan_type: 'trial' } })
    const store = useUserStore()
    const res = await store.register({ phone: '13900000000', password: 'abc12345', tenant_name: 'X' })
    expect(res.tenant_id).toBe(3)
    // register 不改登录态
    expect(store.token).toBe('')
  })

  it('logout clears state unconditionally even if API fails', async () => {
    // seed login
    ;(apiLogin as any).mockResolvedValue({ data: { access_token: 't', user_id: 1, tenant_id: 1, role: 'admin' } })
    const store = useUserStore()
    await store.login({ phone: '13800138000', password: 'abc12345' })
    expect(store.isLoggedIn).toBe(true)

    ;(apiLogout as any).mockRejectedValue(new Error('network error'))
    await store.logout() // 不抛错
    expect(store.token).toBe('')
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull()
  })

  it('logout calls api and clears', async () => {
    ;(apiLogin as any).mockResolvedValue({ data: { access_token: 't', user_id: 1, tenant_id: 1, role: 'admin' } })
    const store = useUserStore()
    await store.login({ phone: '13800138000', password: 'abc12345' })
    ;(apiLogout as any).mockResolvedValue({ message: '已登出' })
    await store.logout()
    expect(apiLogout).toHaveBeenCalled()
    expect(store.token).toBe('')
  })

  it('reset action clears all state', () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ token: 'x' }))
    const store = useUserStore()
    store.reset()
    expect(store.token).toBe('')
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull()
  })
})
