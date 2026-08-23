/** 批次 3：客服回复契约、Trace 排序与流式 CSRF 回归。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('agent message and trace contracts', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it('uses the dedicated agent-message endpoint', async () => {
    const post = vi.fn().mockResolvedValue({ data: { message_id: 1, role: 'agent' } })
    vi.doMock('@/api/request', () => ({
      default: { get: vi.fn(), post },
      getAuthToken: vi.fn(() => ''),
      handleAuthFailure: vi.fn(),
      ApiError: class extends Error {},
    }))
    const { sendAgentMessage } = await import('@/api/session')
    await sendAgentMessage(9, '人工回复')
    expect(post).toHaveBeenCalledWith('/session/9/agent-message', { content: '人工回复' })
  })

  it('selects traces[0] because backend returns newest first', async () => {
    vi.doMock('@/api/request', () => ({
      default: { get: vi.fn(), post: vi.fn() },
      getAuthToken: vi.fn(() => ''),
      handleAuthFailure: vi.fn(),
      ApiError: class extends Error {},
    }))
    const { selectLatestTrace } = await import('@/api/session')
    const traces = [{ _id: 'newest' }, { _id: 'older' }] as any
    expect(selectLatestTrace(traces)?._id).toBe('newest')
  })
})

describe('stream CSRF contract', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.setItem('zhihub:user', JSON.stringify({ token: 'jwt-token' }))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('sends the same token in Authorization and X-CSRF-Token', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('data: [DONE]\n\n'))
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, body })
    vi.stubGlobal('fetch', fetchMock)
    const { sendMessageStream } = await import('@/api/session')

    await sendMessageStream(1, 'hello', () => {})
    const headers = fetchMock.mock.calls[0][1].headers
    expect(headers.Authorization).toBe('Bearer jwt-token')
    expect(headers['X-CSRF-Token']).toBe('jwt-token')
  })

  it('clears auth and exposes 403 instead of allowing a fallback request', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      body: null,
      json: vi.fn().mockResolvedValue({ detail: '缺少 CSRF 令牌' }),
    }))
    const { sendMessageStream } = await import('@/api/session')

    await expect(sendMessageStream(1, 'hello', () => {})).rejects.toMatchObject({ status: 403 })
    expect(localStorage.getItem('zhihub:user')).toBeNull()
  })
})
