# -*- coding: utf-8 -*-
import io
ROOT = 'C:/Users/Lenovo/Desktop/Three/zhihub/frontend'
def wr(p, s):
    io.open(p, 'w', encoding='utf-8').write(s); print(f'[OK] {p}')

# --- T051: session.ts add sendMessageStream ---
p = f'{ROOT}/src/api/session.ts'
s = io.open(p, encoding='utf-8').read()
if 'sendMessageStream' not in s:
    s += '''

// ---- 013 US7: SSE 流式响应 ----

/**
 * 流式发送消息：POST /session/message/stream，逐字回调 onChunk，[DONE] 结束。
 * 用 fetch + ReadableStream 解析 SSE（非 EventSource，因需 POST）。
 */
export async function sendMessageStream(
  sessionId: number,
  content: string,
  onChunk: (chunk: string) => void,
): Promise<void> {
  const raw = localStorage.getItem('zhihub:user')
  const token = raw ? (JSON.parse(raw).token ?? '') : ''
  const resp = await fetch('/api/v1/session/message/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ session_id: sessionId, content }),
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`流式请求失败: ${resp.status}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6)
      if (payload === '[DONE]') return
      try {
        const obj = JSON.parse(payload)
        if (obj.chunk) onChunk(obj.chunk)
        else if (obj.error) throw new Error(obj.error)
      } catch (e) {
        if (e instanceof SyntaxError) continue
        throw e
      }
    }
  }
}
'''
    wr(p, s)

# --- T055: ws-client.ts ---
wr(f'{ROOT}/src/utils/ws-client.ts', '''\
/**
 * src/utils/ws-client.ts
 * 013 US8: WebSocket 客户端 -- 连接坐席工作台实时推送，指数退避重连。
 */
export interface WsMessage {
  type: string
  [key: string]: unknown
}

export class WsClient {
  private ws: WebSocket | null = null
  private url: string
  private listeners: Array<(msg: WsMessage) => void> = []
  private reconnectAttempts = 0
  private shouldReconnect = true
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  constructor(url: string) {
    this.url = url
  }

  connect(): void {
    this.shouldReconnect = true
    this.ws = new WebSocket(this.url)
    this.ws.onopen = () => {
      this.reconnectAttempts = 0
    }
    this.ws.onmessage = (e: MessageEvent) => {
      try {
        const msg = JSON.parse(e.data) as WsMessage
        this.listeners.forEach((cb) => cb(msg))
      } catch {
        // 非 JSON 心跳，忽略
      }
    }
    this.ws.onclose = () => {
      if (this.shouldReconnect) this.scheduleReconnect()
    }
    this.ws.onerror = () => {
      this.ws?.close()
    }
  }

  onMessage(cb: (msg: WsMessage) => void): void {
    this.listeners.push(cb)
  }

  send(data: WsMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000)
    this.reconnectAttempts++
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  close(): void {
    this.shouldReconnect = false
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
  }
}
''')

# --- T050: session-api.spec.ts add sendMessageStream test ---
p = f'{ROOT}/tests/unit/session-api.spec.ts'
s = io.open(p, encoding='utf-8').read()
if 'sendMessageStream' not in s:
    s += '''

// ---- 013 US7 (T050): sendMessageStream SSE 解析 ----
describe('sendMessageStream (013 US7)', () => {
  it('parses SSE chunks and calls onChunk', async () => {
    const { sendMessageStream } = await import('@/api/session')
    const chunks: string[] = []
    // mock fetch 返回 ReadableStream
    const encoder = new TextEncoder()
    const body = new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(encoder.encode('data: {"chunk":"H"}\\n\\n'))
        ctrl.enqueue(encoder.encode('data: {"chunk":"i"}\\n\\n'))
        ctrl.enqueue(encoder.encode('data: [DONE]\\n\\n'))
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
})
'''
    wr(p, s)

# --- T054: ws-client.spec.ts ---
wr(f'{ROOT}/tests/unit/ws-client.spec.ts', '''\
/**
 * tests/unit/ws-client.spec.ts
 * 013 US8 (T054): WsClient 连接/消息分发/重连/关闭
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

describe('WsClient (013 US8)', () => {
  it('connects and dispatches messages to listeners', () => {
    const client = new WsClient('ws://test')
    const received: any[] = []
    client.onMessage((m) => received.push(m))
    client.connect()
    const ws = MockWS.instances[0]
    ws.onopen?.()
    ws.onmessage?.({ data: JSON.stringify({ type: 'new_session', session_id: 1 }) })
    expect(received).toHaveLength(1)
    expect(received[0].type).toBe('new_session')
  })

  it('schedules reconnect on close', () => {
    vi.useFakeTimers()
    const client = new WsClient('ws://test')
    client.connect()
    const ws = MockWS.instances[0]
    ws.onclose?.()
    // 应安排重连
    vi.advanceTimersBySeconds(2)
    expect(MockWS.instances.length).toBeGreaterThanOrEqual(2)
    client.close()
    vi.useRealTimers()
  })

  it('close() stops reconnect', () => {
    vi.useFakeTimers()
    const client = new WsClient('ws://test')
    client.connect()
    client.close()
    const countBefore = MockWS.instances.length
    vi.advanceTimersBySeconds(35)
    expect(MockWS.instances.length).toBe(countBefore) // 不再重连
    vi.useRealTimers()
  })

  it('send() forwards JSON when open', () => {
    const client = new WsClient('ws://test')
    client.connect()
    const ws = MockWS.instances[0]
    ws.onopen?.()
    client.send({ type: 'typing', session_id: 1, is_typing: true })
    expect(ws.sent).toHaveLength(1)
    expect(JSON.parse(ws.sent[0]).type).toBe('typing')
  })
})
''')

print('--- T050/T051/T054/T055 done ---')
