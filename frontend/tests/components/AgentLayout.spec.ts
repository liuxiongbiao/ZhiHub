/**
 * tests/components/AgentLayout.spec.ts
 *
 * : AgentLayout 菜单内聚验证——坐席侧菜单项必须全部指向 /agent/* 路由，
 * 不允许跳回 AdminLayout 下的 /ticket/list、/order/* 管理端路径。
 *
 *  收尾三: 会话操作验证——当前客服身份展示、个人中心入口（/agent/profile，
 * 复用 Profile.vue 但留在 AgentLayout）、退出登录必须走 userStore.logout() 整体清理。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'
import AgentLayout from '@/layouts/AgentLayout.vue'
import { useUserStore } from '@/store/modules/user'

let router: ReturnType<typeof createRouter>
beforeEach(() => {
  setActivePinia(createPinia())
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/agent',
        component: AgentLayout,
        children: [
          { path: 'console', name: 'agent-console', component: { template: '<div>console</div>' } },
          { path: 'tickets', name: 'agent-tickets', component: { template: '<div>tickets</div>' } },
          { path: 'orders', name: 'agent-orders', component: { template: '<div>orders</div>' } },
          { path: 'refunds', name: 'agent-refunds', component: { template: '<div>refunds</div>' } },
          { path: 'profile', name: 'agent-profile', component: { template: '<div>profile</div>' } },
        ],
      },
      { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
      { path: '/profile', name: 'admin-profile', component: { template: '<div>admin profile</div>' } },
    ],
  })
})

function mountLayout() {
  // 不额外传新 pinia：组件内 useUserStore() 必须取到 beforeEach 里 setActivePinia
  // 的同一个实例，否则 phone/role 断言读到空 store
  return mount(AgentLayout, { global: { plugins: [router, ElementPlus] } })
}

function menuIndexes(): string[] {
  const w = mountLayout()
  // el-menu-item 的 index prop 即 router 模式下的跳转目标
  return w.findAllComponents({ name: 'ElMenuItem' }).map((i) => i.props('index') as string)
}

describe('AgentLayout menu ()', () => {
  it('all menu entries point to /agent/* routes', () => {
    const indexes = menuIndexes()
    expect(indexes.length).toBeGreaterThanOrEqual(4)
    for (const idx of indexes) {
      expect(idx.startsWith('/agent/'), `menu index "${idx}" should stay under /agent/*`).toBe(true)
    }
  })

  it('covers console / tickets / orders / refunds', () => {
    const indexes = menuIndexes()
    expect(indexes).toContain('/agent/console')
    expect(indexes).toContain('/agent/tickets')
    expect(indexes).toContain('/agent/orders')
    expect(indexes).toContain('/agent/refunds')
  })

  it('no menu entry jumps back to AdminLayout pages', () => {
    const indexes = menuIndexes()
    expect(indexes).not.toContain('/ticket/list')
    expect(indexes).not.toContain('/order/list')
    expect(indexes).not.toContain('/order/refunds')
  })
})

describe('AgentLayout session ops ( 收尾三)', () => {
  it('shows current agent identity (phone) in the header', async () => {
    const userStore = useUserStore()
    userStore.$patch({ token: 't', role: 'agent', phone: '13900139000' })
    router.push('/agent/console')
    await router.isReady()
    const w = mountLayout()
    expect(w.text()).toContain('13900139000')
  })

  it('user dropdown offers profile at /agent/profile (not AdminLayout /profile) and logout', async () => {
    const userStore = useUserStore()
    userStore.$patch({ token: 't', role: 'agent', phone: '13900139000' })
    router.push('/agent/console')
    await router.isReady()
    const w = mountLayout()
    // el-dropdown 菜单 teleport 到 body：展开断言真实 DOM 中有两个入口（真实点击由浏览器 E2E 覆盖）
    await w.find('.user-trigger').trigger('mouseenter')
    await flushPromises()
    expect(document.body.textContent).toContain('个人中心')
    expect(document.body.textContent).toContain('退出登录')
    // command=profile 必须落在 /agent/profile，不得跳回 AdminLayout 的 /profile
    w.findComponent({ name: 'ElDropdown' }).vm.$emit('command', 'profile')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/agent/profile')
  })

  it('logout calls userStore.logout() (full reset) and lands on /login', async () => {
    const userStore = useUserStore()
    userStore.$patch({ token: 't', refresh_token: 'r', role: 'agent', phone: '13900139000', user_id: 5, tenant_id: 1 })
    const logoutSpy = vi.spyOn(userStore, 'logout').mockResolvedValue(undefined)
    router.push('/agent/console')
    await router.isReady()
    const w = mountLayout()
    w.findComponent({ name: 'ElDropdown' }).vm.$emit('command', 'logout')
    await flushPromises()
    expect(logoutSpy).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.path).toBe('/login')
  })
})
