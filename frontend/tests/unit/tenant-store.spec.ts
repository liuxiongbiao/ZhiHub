import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/tenant', () => ({ getTenantHome: vi.fn() }))

const { getTenantHome } = await import('@/api/tenant')
const { useTenantStore } = await import('@/store/modules/tenant')

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('useTenantStore', () => {
  it('fetchHome populates state from API', async () => {
    ;(getTenantHome as any).mockResolvedValue({
      data: {
        tenant_id: 1, tenant_name: '示例科技', plan_type: 'trial',
        status: 'active', current_user: { user_id: 1, role: 'admin' },
      },
    })
    const store = useTenantStore()
    await store.fetchHome()
    expect(store.tenant_name).toBe('示例科技')
    expect(store.plan_type).toBe('trial')
    expect(store.current_user?.role).toBe('admin')
  })

  it('rejects on API error', async () => {
    ;(getTenantHome as any).mockRejectedValue(new Error('network'))
    const store = useTenantStore()
    await expect(store.fetchHome()).rejects.toThrow('network')
  })
})
