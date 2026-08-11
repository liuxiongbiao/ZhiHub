/**
 * tests/integration/auth-flow.spec.ts
 *
 * 守卫 + 路由 + store + mocked API 全链路。
 * 每用例新建内存 router(import fresh registerGuard/wireUnauthorized)。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory, type RouteRecordRaw } from 'vue-router'
import { AUTH_STORAGE_KEY } from '@/constants'

vi.mock('@/api/auth', () => ({ login: vi.fn(), register: vi.fn(), logout: vi.fn(), loginSms: vi.fn(), loginEmail: vi.fn(), smsSend: vi.fn(), registerEmail: vi.fn(), wechatAuth: vi.fn(), wechatCallback: vi.fn() }))
vi.mock('@/api/tenant', () => ({ getTenantHome: vi.fn(), getWizardStatus: vi.fn(), advanceWizard: vi.fn(), inviteMember: vi.fn(), getAuditLogs: vi.fn(), updateWidgetConfig: vi.fn(), getWidgetEmbedCode: vi.fn() }))

const { login: apiLogin, logout: apiLogout } = await import('@/api/auth')
const { getTenantHome } = await import('@/api/tenant')
import { useUserStore } from '@/store/modules/user'
import { registerGuard, wireUnauthorized } from '@/permission'

const HomeStub = { template: '<div>home</div>' }
const LoginStub = { template: '<div>login-page</div>' }

const ROUTES: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: LoginStub, meta: { public: true } },
  { path: '/register', name: 'register', component: { template: '<div>reg</div>' }, meta: { public: true } },
  {
    path: '/',
    component: { template: '<router-view/>' },
    children: [{ path: '', name: 'dashboard', component: HomeStub }],
  },
]

function seedLogin(store: ReturnType<typeof useUserStore>) {
  store.$patch({ token: 'fake-jwt', user_id: 1, tenant_id: 1, role: 'admin' })
  store.persist()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.clear()
})

function makeRouter() {
  const r = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  registerGuard(r)
  wireUnauthorized(r)
  return r
}

describe('auth-flow integration', () => {
  it('unauthenticated user redirected to /login', async () => {
    const router = makeRouter()
    await router.push('/')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/')
  })

  it('authenticated user accesses dashboard', async () => {
    const store = useUserStore()
    seedLogin(store)
    const router = makeRouter()
    await router.push('/')
    expect(router.currentRoute.value.name).toBe('dashboard')
  })

  it('authenticated user visiting /login redirected to /', async () => {
    const store = useUserStore()
    seedLogin(store)
    const router = makeRouter()
    await router.push('/login')
    expect(router.currentRoute.value.path).toBe('/')
  })

  it('login action -> can access dashboard', async () => {
    ;(apiLogin as any).mockResolvedValue({ data: { access_token: 'jwt-x', user_id: 1, tenant_id: 1, role: 'admin' } })
    const store = useUserStore()
    await store.login({ phone: '13800138000', password: 'abc12345' })

    const router = makeRouter()
    await router.push('/')
    expect(router.currentRoute.value.name).toBe('dashboard')
    expect(store.token).toBe('jwt-x')
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeTruthy()
  })

  it('logout clears state and redirects to /login', async () => {
    ;(apiLogout as any).mockResolvedValue({ message: '已登出' })
    const store = useUserStore()
    seedLogin(store)

    const router = makeRouter()
    await store.logout()
    expect(store.token).toBe('')
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull()

    await router.push('/')
    expect(router.currentRoute.value.name).toBe('login')
  })
})
