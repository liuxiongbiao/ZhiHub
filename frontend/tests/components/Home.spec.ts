/**
 * frontend/tests/components/Home.spec.ts
 *
 * 被测对象: Home.vue（管理后台工作台首页）。
 * 覆盖场景: 快捷入口按角色渲染 —— 坐席登录时不应看到管理员专属入口。
 */
import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import ElementPlus from 'element-plus'

const apiMocks = vi.hoisted(() => ({
  getTenantHome: vi.fn(),
  getWizardStatus: vi.fn(),
  getAnalyticsOverview: vi.fn(),
}))

vi.mock('@/api/tenant', () => ({
  getTenantHome: apiMocks.getTenantHome,
  getWizardStatus: apiMocks.getWizardStatus,
}))
vi.mock('@/api/analytics', () => ({ getAnalyticsOverview: apiMocks.getAnalyticsOverview }))

import Home from '@/views/dashboard/Home.vue'
import { useTenantStore } from '@/store/modules/tenant'

describe('Home.vue role-aware shortcuts', () => {
  it('does not render admin-only shortcuts for an agent', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useTenantStore().$patch({
      tenant_id: 1,
      tenant_name: 'Agent tenant',
      plan_type: 'enterprise',
      status: 'active',
      current_user: { user_id: 2, role: 'agent' },
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: Home }],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(Home, { global: { plugins: [pinia, router, ElementPlus] } })
    await flushPromises()

    expect(wrapper.text()).not.toContain('知识库')
    expect(wrapper.text()).not.toContain('成员邀请')
    expect(wrapper.text()).toContain('工单看板')
    expect(wrapper.text()).toContain('订单列表')
  })

  it('loads the admin identity before requesting live dashboard metrics', async () => {
    apiMocks.getTenantHome.mockResolvedValueOnce({
      data: {
        tenant_id: 1,
        tenant_name: '智枢演示企业',
        plan_type: 'enterprise',
        status: 'active',
        current_user: { user_id: 1, role: 'admin' },
        degradation_alert: false,
        warning_banner: null,
      },
    })
    apiMocks.getAnalyticsOverview.mockResolvedValueOnce({
      data: {
        summary: {
          auto_resolution_rate: 84.6,
          waiting_human: 2,
          pending_refunds: 2,
          knowledge_issues: 0,
        },
        attention_items: [],
      },
    })
    apiMocks.getWizardStatus.mockResolvedValueOnce({
      data: {
        current_step: 'done',
        completed: true,
        total_steps: 4,
        steps: ['basic', 'knowledge', 'mcp', 'team'],
        checks: [],
      },
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: Home }],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(Home, { global: { plugins: [pinia, router, ElementPlus] } })
    await flushPromises()

    expect(apiMocks.getTenantHome).toHaveBeenCalledOnce()
    expect(apiMocks.getAnalyticsOverview).toHaveBeenCalledWith(30)
    expect(apiMocks.getWizardStatus).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('84.6%')
    expect(wrapper.text()).toContain('等待人工2')
    expect(wrapper.text()).toContain('待审批退款2')
    expect(wrapper.text()).toContain('上线准备度100%')
  })
})
