/**
 * src/utils/ws-client.ts
 * 规格 013 US8: WebSocket 客户端 —— 连接坐席工作台实时推送，指数退避重连。
 */
export interface WsMessage {
  type: string
  payload: Record<string, unknown>
}

export type WsOutboundMessage = { type: string; [key: string]: unknown }

export class WsClient {
  private ws: WebSocket | null = null
  private url: string
  private tokenProvider: (() => Promise<string>) | null
  private listeners: Array<(msg: WsMessage) => void> = []
  private reconnectAttempts = 0
  private shouldReconnect = true
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null

  constructor(url: string, tokenProvider?: () => Promise<string>) {
    this.url = url
    this.tokenProvider = tokenProvider ?? null
  }

  private async resolveUrl(): Promise<string> {
    if (!this.tokenProvider) return this.url
    const token = await this.tokenProvider()
    const u = new URL(this.url)
    u.searchParams.set('token', token)
    return u.toString()
  }

  async connect(): Promise<void> {
    this.shouldReconnect = true
    let url: string
    try {
      url = await this.resolveUrl()
    } catch {
      url = this.url
    }
    this.ws = new WebSocket(url)
    this.ws.onopen = () => {
      this.reconnectAttempts = 0
      this.startHeartbeat()
    }
    this.ws.onmessage = (e: MessageEvent) => {
      try {
        const raw = JSON.parse(e.data) as Record<string, unknown>
        const type = String(raw.type ?? raw.event ?? '')
        const payload = raw.payload && typeof raw.payload === 'object'
          ? raw.payload as Record<string, unknown>
          : Object.fromEntries(Object.entries(raw).filter(([key]) => !['type', 'event'].includes(key)))
        this.listeners.forEach((cb) => cb({ type, payload }))
      } catch {
        // 非 JSON 心跳，忽略
      }
    }
    this.ws.onclose = () => {
      this.stopHeartbeat()
      if (this.shouldReconnect) this.scheduleReconnect()
    }
    this.ws.onerror = () => {
      this.ws?.close()
    }
  }

  onMessage(cb: (msg: WsMessage) => void): void {
    this.listeners.push(cb)
  }

  send(data: WsOutboundMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000)
    this.reconnectAttempts++
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null
      await this.connect()
    }, delay)
  }

  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: 'heartbeat' })
    }, 30_000)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  close(): void {
    this.shouldReconnect = false
    this.stopHeartbeat()
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
  }
}
