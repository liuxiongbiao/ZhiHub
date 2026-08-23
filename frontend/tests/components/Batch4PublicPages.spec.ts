/**
 * 邮件激活与接受邀请公开页面状态测试。
 */
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { activateEmail, acceptInvite } = vi.hoisted(() => ({
  activateEmail: vi.fn(),
  acceptInvite: vi.fn(),
}))
vi.mock('@/api/auth', () => ({ activateEmail }))
vi.mock('@/api/tenant', () => ({ acceptInvite }))

import ActivateEmail from '@/views/login/ActivateEmail.vue'
import AcceptInvite from '@/views/onboarding/AcceptInvite.vue'
import { useUserStore } from '@/store/modules/user'

function makeRouter(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/activate-email', component: ActivateEmail },
      { path: '/accept-invite', component: AcceptInvite },
      { path: '/login', component: { template: '<div>login</div>' } },
      { path: '/', component: { template: '<div>home</div>' } },
    ],
  })
  return router.push(path).then(() => router.isReady()).then(() => router)
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('ActivateEmail', () => {
  it('shows success after activating the query token', async () => {
    activateEmail.mockResolvedValue({ data: { activated: true } })
    const router = await makeRouter('/activate-email?token=valid-token-123456')
    const wrapper = mount(ActivateEmail, { global: { plugins: [router, ElementPlus] } })
    await flushPromises()
    expect(activateEmail).toHaveBeenCalledWith('valid-token-123456')
    expect(wrapper.text()).toContain('激活成功')
  })

  it('shows expired state for a rejected token', async () => {
    activateEmail.mockRejectedValue({ status: 400, message: '激活链接无效或已过期' })
    const router = await makeRouter('/activate-email?token=expired-token-1234')
    const wrapper = mount(ActivateEmail, { global: { plugins: [router, ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('已过期')
  })
})

describe('AcceptInvite', () => {
  it('guides an anonymous user to login and preserves the return URL', async () => {
    const router = await makeRouter('/accept-invite?token=invite-token')
    const wrapper = mount(AcceptInvite, { global: { plugins: [router, ElementPlus] } })
    await wrapper.get('[data-test="login-first"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/accept-invite?token=invite-token')
  })

  it('lets a logged-in user confirm joining and handles expired tokens', async () => {
    const user = useUserStore()
    user.$patch({ token: 'jwt', user_id: 1, tenant_id: 1, role: 'agent', phone: '13900000000' })
    acceptInvite.mockRejectedValue({ status: 410, message: '邀请链接无效或已过期' })
    const router = await makeRouter('/accept-invite?token=expired-token')
    const wrapper = mount(AcceptInvite, { global: { plugins: [router, ElementPlus] } })
    await wrapper.get('[data-test="accept-invite"]').trigger('click')
    await flushPromises()
    expect(acceptInvite).toHaveBeenCalledWith({ token: 'expired-token' })
    expect(wrapper.text()).toContain('重新邀请')
  })
})
