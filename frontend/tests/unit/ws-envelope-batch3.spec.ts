/** 批次 3：WebSocket envelope 与心跳回归。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WsClient } from '@/utils/ws-client'

class MockWS {
  static instances: MockWS[] = []
  static OPEN = 1
  readyState = 1
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  sent: string[] = []
  constructor(public url: string) { MockWS.instances.push(this) }
  send(data: string) { this.sent.push(data) }
  close() { this.onclose?.() }
}

beforeEach(() => {
  MockWS.instances = []
  vi.stubGlobal('WebSocket', MockWS)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('WsClient batch3 envelope', () => {
  it('dispatches type and payload without flattened field access', async () => {
    const client = new WsClient('ws://test')
    const received: any[] = []
    client.onMessage((message) => received.push(message))
    await client.connect()
    MockWS.instances[0].onmessage?.({
      data: JSON.stringify({ type: 'session_claimed', payload: { session_id: 8, agent_id: 2 } }),
    })
    expect(received[0]).toEqual({
      type: 'session_claimed',
      payload: { session_id: 8, agent_id: 2 },
    })
    client.close()
  })

  it('starts heartbeat on open and stops it on close', async () => {
    vi.useFakeTimers()
    const client = new WsClient('ws://test')
    await client.connect()
    const ws = MockWS.instances[0]
    ws.onopen?.()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(ws.sent.map((item) => JSON.parse(item).type)).toContain('heartbeat')
    const sentBeforeClose = ws.sent.length
    client.close()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(ws.sent).toHaveLength(sentBeforeClose)
  })
})
