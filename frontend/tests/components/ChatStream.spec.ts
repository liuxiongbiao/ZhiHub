/**
 * tests/components/ChatStream.spec.ts
 * 013 US7 (T052): ChatWindow 流式发送逐字渲染 chunk
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

const { mocks } = vi.hoisted(() => ({
  mocks: {
    getSessionDetail: vi.fn(),
    sendMessage: vi.fn(),
    sendMessageStream: vi.fn(),
    submitCsat: vi.fn(),
    leaveMessage: vi.fn(),
  }
}))
vi.mock('@/api/session', () => mocks)
vi.mock('element-plus', async (o) => {
  const a = await o<typeof import('element-plus')>()
  return { ...a, ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }
})

import ChatWindow from '@/components/chat/ChatWindow.vue'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

function mountWindow() {
  return mount(ChatWindow, {
    props: { sessionId: 1 },
    global: { plugins: [createPinia(), ElementPlus] },
  })
}

describe('ChatWindow SSE 流式 (013 US7)', () => {
  it('streams chunks into the assistant message bubble', async () => {
    mocks.getSessionDetail.mockResolvedValue({ data: { session_id: 1, status: 'agent_handling', messages: [] } })
    // 流式回调：先 H 后 i
    mocks.sendMessageStream.mockImplementation((_sid: number, _content: string, onChunk: (c: string) => void) => {
      onChunk('H')
      onChunk('i')
      return Promise.resolve()
    })
    const w = mountWindow()
    await flushPromises()

    const input = w.find('input')
    await input.setValue('你好')
    await input.trigger('keyup.enter')
    await flushPromises()

    expect(mocks.sendMessageStream).toHaveBeenCalledWith(1, '你好', expect.any(Function), expect.any(Function))
    // 最后一条消息应为 assistant，内容为拼接后的 "Hi"
    const bubbles = w.findAll('.message-bubble')
    const last = bubbles[bubbles.length - 1]
    expect(last.exists()).toBe(true)
    expect(last.text()).toContain('Hi')
  })

  it('falls back to error message when stream throws', async () => {
    mocks.getSessionDetail.mockResolvedValue({ data: { session_id: 1, status: 'agent_handling', messages: [] } })
    mocks.sendMessageStream.mockRejectedValue(new Error('stream failed'))
    const w = mountWindow()
    await flushPromises()

    const input = w.find('input')
    await input.setValue('ping')
    await input.trigger('keyup.enter')
    await flushPromises()

    const bubbles = w.findAll('.message-bubble')
    const last = bubbles[bubbles.length - 1]
    expect(last.text()).toContain('发送失败')
  })
})
