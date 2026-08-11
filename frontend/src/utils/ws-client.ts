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
