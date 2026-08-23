/**
 * tests/unit/session-api.spec.ts
 * 规格 013 US3/US4/US5 (T026/T029/T038): getTrace/returnToAgent/submitCsat/leaveMessage/getQuickReplies
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
const { mockRequest } = vi.hoisted(() => ({
  mockRequest: { get: vi.fn(), post: vi.fn() }
}))
vi.mock('@/api/request', () => ({ default: mockRequest }))
import { getTrace, returnToAgent, submitCsat, leaveMessage, getQuickReplies } from '@/api/session'
beforeEach(() => { vi.clearAllMocks() })
describe('session api (规格 013 US3/US4/US5)', () => {
  it('getTrace gets /session/{id}/trace', async () => {
    mockRequest.get.mockResolvedValue({ data: { traces: [] } })
    await getTrace(9)
    expect(mockRequest.get).toHaveBeenCalledWith('/session/9/trace')
  })
  it('returnToAgent posts to /session/{id}/return-to-agent', async () => {
    mockRequest.post.mockResolvedValue({ data: { status: 'agent_handling' } })
    await returnToAgent(9)
    expect(mockRequest.post).toHaveBeenCalledWith('/session/9/return-to-agent')
  })
  it('submitCsat posts to /session/{id}/csat', async () => {
    mockRequest.post.mockResolvedValue({ data: { id: 1 } })
    await submitCsat(9, 5, 'good')
    expect(mockRequest.post).toHaveBeenCalledWith('/session/9/csat', { score: 5, comment: 'good' })
  })
  it('leaveMessage posts to /session/{id}/leave-message', async () => {
    mockRequest.post.mockResolvedValue({ data: { ticket_no: '' } })
    await leaveMessage(9, 'help')
    expect(mockRequest.post).toHaveBeenCalledWith('/session/9/leave-message', { content: 'help' })
  })
  it('getQuickReplies gets /session/quick-replies', async () => {
    mockRequest.get.mockResolvedValue({ data: [] })
    await getQuickReplies()
    expect(mockRequest.get).toHaveBeenCalledWith('/session/quick-replies')
  })
})


// ———— 规格 013 US7 (T050): sendMessageStream SSE 解析 ————
describe('sendMessageStream (规格 013 US7)', () => {
  it('parses SSE chunks and calls onChunk', async () => {
    const { sendMessageStream } = await import('@/api/session')
    const chunks: string[] = []
    // mock fetch 返回 ReadableStream
    const encoder = new TextEncoder()
    const body = new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(encoder.encode('data: {"chunk":"H"}\n\n'))
        ctrl.enqueue(encoder.encode('data: {"chunk":"i"}\n\n'))
        ctrl.enqueue(encoder.encode('data: [DONE]\n\n'))
        ctrl.close()
      },
    })
    const fakeFetch = vi.fn().mockResolvedValue({ ok: true, body })
    vi.stubGlobal('fetch', fakeFetch)
    localStorage.setItem('zhihub:user', JSON.stringify({ token: 't' }))

    await sendMessageStream(1, 'hi', (c) => chunks.push(c))
    expect(chunks).toEqual(['H', 'i'])
    vi.unstubAllGlobals()
  })

  it('parses message_id event and calls onMeta', async () => {
    const { sendMessageStream } = await import('@/api/session')
    const encoder = new TextEncoder()
    const body = new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(encoder.encode('data: {"chunk":"A"}\n\n'))
        ctrl.enqueue(encoder.encode('data: {"message_id":88}\n\n'))
        ctrl.enqueue(encoder.encode('data: [DONE]\n\n'))
        ctrl.close()
      },
    })
    const fakeFetch = vi.fn().mockResolvedValue({ ok: true, body })
    vi.stubGlobal('fetch', fakeFetch)
    localStorage.setItem('zhihub:user', JSON.stringify({ token: 't' }))

    const meta: number[] = []
    await sendMessageStream(1, 'hi', () => {}, (mid) => meta.push(mid))
    expect(meta).toEqual([88])
    vi.unstubAllGlobals()
  })
})
