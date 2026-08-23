/**
 * tests/unit/ws-client.spec.ts
 * 规格 013 US8 (T054): WsClient 连接/消息分发/重连/关闭
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { WsClient } from '@/utils/ws-client'

class MockWS {
  static instances: MockWS[] = []
  static OPEN = 1
  static CLOSED = 3
  readyState = 1
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  sent: string[] = []
  constructor(public url: string) { MockWS.instances.push(this) }
  send(data: string) { this.sent.push(data) }
  close() { this.onclose?.() }
}

beforeEach(() => { MockWS.instances = []; vi.stubGlobal('WebSocket', MockWS) })
afterEach(() => { vi.unstubAllGlobals() })

describe('WsClient (规格 013 US8)', () => {
  it('connects and dispatches messages to listeners', async () => {
    const client = new WsClient('ws://test')
    const received: any[] = []
    client.onMessage((m) => received.push(m))
    await client.connect()
    const ws = MockWS.instances[0]
    ws.onopen?.()
    ws.onmessage?.({ data: JSON.stringify({ type: 'new_session', session_id: 1 }) })
    expect(received).toHaveLength(1)
    expect(received[0].type).toBe('new_session')
  })

  it('schedules reconnect on close', async () => {
    vi.useFakeTimers()
    const client = new WsClient('ws://test')
    await client.connect()
    const ws = MockWS.instances[0]
    ws.onclose?.()
    // 应安排重连
    await vi.advanceTimersByTimeAsync(2000)
    expect(MockWS.instances.length).toBeGreaterThanOrEqual(2)
    client.close()
    vi.useRealTimers()
  })

  it('close() stops reconnect', async () => {
    vi.useFakeTimers()
    const client = new WsClient('ws://test')
    await client.connect()
    client.close()
    const countBefore = MockWS.instances.length
    await vi.advanceTimersByTimeAsync(35000)
    expect(MockWS.instances.length).toBe(countBefore) // 不再重连
    vi.useRealTimers()
  })

  it('send() forwards JSON when open', async () => {
    const client = new WsClient('ws://test')
    await client.connect()
    const ws = MockWS.instances[0]
    ws.onopen?.()
    client.send({ type: 'typing', session_id: 1, is_typing: true })
    expect(ws.sent).toHaveLength(1)
    expect(JSON.parse(ws.sent[0]).type).toBe('typing')
  })
})
