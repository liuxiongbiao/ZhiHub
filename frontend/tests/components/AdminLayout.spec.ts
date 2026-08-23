/**
 * frontend/tests/components/AdminLayout.spec.ts
 *
 * 被测对象: AdminLayout.vue（企业管理后台布局）。
 * 覆盖场景: 布局挂载、租户信息加载与菜单渲染行为。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import ElementPlus from 'element-plus'

vi.mock('@/api/tenant', () => ({ getTenantHome: vi.fn() }))
vi.mock('element-plus', async (o) => {
  const a = await o<typeof import('element-plus')>()
  return { ...a, ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }
})

import AdminLayout from '@/layouts/AdminLayout.vue'
import { useTenantStore } from '@/store/modules/tenant'
const { getTenantHome } = await import('@/api/tenant')

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.clearAllMocks()
})

function mountLayout() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/',
        component: AdminLayout,
        children: [{ path: '', component: { template: '<div>dashboard</div>' } }],
      },
      { path: '/login', name: 'login', component: { template: '<div/>' } },
    ],
  })
  return mount(AdminLayout, { global: { plugins: [createPinia(), router, ElementPlus] } })
}

describe('AdminLayout.vue', () => {
  it('renders layout and fetches tenant home', async () => {
    ;(getTenantHome as any).mockResolvedValue({
      data: {
        tenant_id: 1,
        tenant_name: '示例科技',
        plan_type: 'trial',
        status: 'active',
        current_user: { user_id: 1, role: 'admin' },
      },
    })
    const w = mountLayout()
    await flushPromises()
    expect(getTenantHome).toHaveBeenCalled()
  })

  it('shows tenant name after fetch', async () => {
    ;(getTenantHome as any).mockResolvedValue({
      data: {
        tenant_id: 1,
        tenant_name: '测试公司',
        plan_type: 'trial',
        status: 'active',
        current_user: { user_id: 1, role: 'admin' },
      },
    })
    const w = mountLayout()
    await flushPromises()
    expect(w.html()).toContain('测试公司')
  })

  it('renders sidebar menu', () => {
    const w = mountLayout()
    expect(w.html()).toContain('工作台')
  })

  it('has logout area', async () => {
    const w = mountLayout()
    // 退出入口（用户下拉触发区）存在
    expect(w.find('.user-trigger').exists()).toBe(true)
    // el-dropdown 菜单 teleport 到 body：展开后断言真实 DOM 中出现"退出登录"
    await w.find('.user-trigger').trigger('mouseenter')
    await flushPromises()
    expect(document.body.textContent).toContain('退出登录')
  })

  it('hides every admin-only navigation entry from agents', () => {
    localStorage.setItem('zhihub:user', JSON.stringify({
      token: 'agent-token', refresh_token: 'refresh-token',
      user_id: 2, tenant_id: 1, role: 'agent', phone: '13800000002',
    }))

    const w = mountLayout()
    const text = w.text()

    expect(text).not.toContain('品牌定制')
    expect(text).not.toContain('知识管理')
    expect(text).not.toContain('会话监控')
    expect(text).not.toContain('计费')
    expect(text).not.toContain('配置向导')
    expect(text).not.toContain('规则配置')
    expect(text).not.toContain('审计日志')
    expect(text).not.toContain('运营看板')
    expect(text).toContain('工单与订单')
  })
})
