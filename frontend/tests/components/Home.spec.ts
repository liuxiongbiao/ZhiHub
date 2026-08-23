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

vi.mock('@/api/tenant', () => ({
  getTenantHome: vi.fn(),
  getWizardStatus: vi.fn(),
}))

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
})
