/**
 * frontend/tests/components/SessionDetail.spec.ts
 *
 * 被测对象: SessionDetail.vue（会话详情页）。
 * 覆盖场景: 非法 session_id 路由参数（非数字）时的容错处理。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ElementPlus from 'element-plus'

const { mocks } = vi.hoisted(() => ({
  mocks: {
    route: { params: { id: 'not-a-number' } },
    getSessionDetail: vi.fn(),
    requestGet: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))
vi.mock('@/api/session', () => ({ getSessionDetail: mocks.getSessionDetail }))
vi.mock('@/api/request', () => ({ default: { get: mocks.requestGet, post: vi.fn() } }))

import SessionDetail from '@/views/session/SessionDetail.vue'

describe('SessionDetail.vue route validation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.params.id = 'not-a-number'
  })

  it('shows a recoverable error state for a non-numeric session id', async () => {
    const wrapper = mount(SessionDetail, { global: { plugins: [ElementPlus] } })
    await flushPromises()

    expect(wrapper.text()).toContain('无效的会话编号')
    expect(wrapper.text()).toContain('返回会话监控')
    expect(mocks.getSessionDetail).not.toHaveBeenCalled()
  })
})
