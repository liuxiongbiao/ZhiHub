/**
 * tests/unit/widget-js.spec.ts
 *
 * : 在 jsdom 中执行真实 widget.js 源码做 DOM 级验证：
 *   - 租户主题色 / Logo / 欢迎语真正生效（安全 DOM API，无 innerHTML）
 *   - 引用显示兼容 sources[].doc 字段
 *   - destroy() 命名空间 API：关闭 WebSocket、清重连定时器、删除 DOM
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import widgetSource from '../../public/widget.js?raw'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  url: string
  readyState = 0
  closed = false
  onopen: ((ev?: unknown) => void) | null = null
  onmessage: ((ev: unknown) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev?: unknown) => void) | null = null
  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
    // 模拟握手成功（下一个宏任务回调 onopen）
    setTimeout(() => { this.readyState = 1; this.onopen && this.onopen({}); }, 0)
  }
  close() {
    this.readyState = 3
    this.closed = true
  }
  send() { /* noop */ }
}

function jsonResponse(status: number, data: unknown) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** jsdom 会把 hex 归一化成 rgb()，断言前转换 */
function hexToRgb(hex: string): string {
  const m = /^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/.exec(hex)!
  return `rgb(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)})`
}

function loadWidget(attrs: Record<string, string> = {}) {
  const script = document.createElement('script')
  const defaults: Record<string, string> = {
    'data-api-key': 'test-key-123',
    'data-endpoint': 'http://localhost:8000/api/v1/widget',
    'data-ws-endpoint': 'ws://localhost:8000/api/v1/ws',
    'data-theme-color': '#1890ff',
  }
  for (const [k, v] of Object.entries({ ...defaults, ...attrs })) script.setAttribute(k, v)
  document.body.appendChild(script)
  Object.defineProperty(document, 'currentScript', { value: script, configurable: true })
  // 执行真实 widget.js（IIFE）
  new Function(widgetSource)()
  Object.defineProperty(document, 'currentScript', { value: null, configurable: true })
  script.remove()
}

function sessionResponse(overrides: Record<string, unknown> = {}) {
  return {
    session_id: 1,
    token: 'widget-token',
    expires_in: 1800,
    welcome_text: '欢迎光临',
    theme_color: '#123456',
    logo_url: 'https://cdn.example.com/logo.png',
    ...overrides,
  }
}

async function openAndSend(fetchMock: ReturnType<typeof vi.fn>) {
  const launcher = document.querySelector<HTMLButtonElement>('.zhihub-widget-launcher')!
  launcher.click()
  const input = document.querySelector<HTMLInputElement>('.zhihub-widget-box input')!
  input.value = '你好'
  const form = document.querySelector<HTMLFormElement>('.zhihub-widget-box form')!
  form.dispatchEvent(new Event('submit', { cancelable: true }))
  // 等 fetch/WS/定时器链走完
  await new Promise((r) => setTimeout(r, 50))
  expect(fetchMock).toHaveBeenCalled()
}

beforeEach(() => {
  document.body.innerHTML = ''
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('widget.js brand application ()', () => {
  it('applies tenant theme, logo and welcome text from session response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, sessionResponse()))
    vi.stubGlobal('fetch', fetchMock)

    loadWidget()
    await openAndSend(fetchMock)

    const header = document.querySelector<HTMLElement>('.zhihub-widget-header')!
    expect(header.style.background).toBe(hexToRgb('#123456'))
    // Logo 用 img 元素创建（安全 DOM API），src 为租户 logo_url
    const logo = header.querySelector<HTMLImageElement>('img')!
    expect(logo).toBeTruthy()
    expect(logo.getAttribute('src')).toBe('https://cdn.example.com/logo.png')
    // 欢迎语出现在消息区
    expect(document.querySelector('.zhihub-widget-log')!.textContent).toContain('欢迎光临')
    // 发送按钮/启动按钮同主题色
    expect(document.querySelector<HTMLElement>('.zhihub-widget-box button')!.style.background).toBe(hexToRgb('#123456'))
    expect(document.querySelector<HTMLElement>('.zhihub-widget-launcher')!.style.background).toBe(hexToRgb('#123456'))
  })

  it('ignores invalid theme from session and keeps the script attribute theme', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, sessionResponse({ theme_color: 'red', logo_url: 'javascript:alert(1)' })))
    vi.stubGlobal('fetch', fetchMock)

    loadWidget()
    await openAndSend(fetchMock)

    const header = document.querySelector<HTMLElement>('.zhihub-widget-header')!
    expect(header.style.background).toBe(hexToRgb('#1890ff'))
    // 非法 logo 不渲染 img
    expect(header.querySelector('img')).toBeNull()
  })

  it.each([
    'https://',
    'https://user:pass@example.com/logo.png',
    'https://example.com/logo.png?x=1',
    'https://example.com/logo.png#x',
    'https://[broken',
    'javascript:alert(1)',
    'https://example.com:99999/logo.png',
    'https://exa\nmple.com/logo.png',
  ])('rejects obviously invalid logo url: %s', async (badLogo) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, sessionResponse({ logo_url: badLogo })))
    vi.stubGlobal('fetch', fetchMock)

    loadWidget()
    await openAndSend(fetchMock)

    expect(document.querySelector('.zhihub-widget-header img')).toBeNull()
  })

  it('renders nothing via innerHTML (XSS-safe rendering)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, sessionResponse({ welcome_text: '<img src=x onerror=alert(1)>' })))
    vi.stubGlobal('fetch', fetchMock)

    loadWidget()
    await openAndSend(fetchMock)

    const log = document.querySelector('.zhihub-widget-log')!
    expect(log.textContent).toContain('<img src=x onerror=alert(1)>')
    expect(log.querySelector('img')).toBeNull()
  })
})

describe('widget.js sources doc field ()', () => {
  it('displays sources[].doc file name instead of generic fallback', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(201, sessionResponse()))
      .mockResolvedValueOnce(jsonResponse(200, {
        message_id: 2,
        role: 'assistant',
        content: '退款政策如下',
        sources: [{ doc: '退款政策.pdf', chunk_index: 2 }],
        metadata: {},
      }))
    vi.stubGlobal('fetch', fetchMock)

    loadWidget()
    await openAndSend(fetchMock)

    const logText = document.querySelector('.zhihub-widget-log')!.textContent!
    expect(logText).toContain('退款政策.pdf')
    expect(logText).toContain('引用')
  })
})

describe('widget.js destroy API ()', () => {
  it('exposes destroy() and removes widget DOM', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(201, sessionResponse())))

    loadWidget()
    expect((window as any).ZhiHubWidget).toBeTruthy()
    expect(typeof (window as any).ZhiHubWidget.destroy).toBe('function')

    ;(window as any).ZhiHubWidget.destroy()
    expect(document.querySelector('#zhihub-widget-root')).toBeNull()
  })

  it('destroy closes the open WebSocket and blocks pending reconnects', async () => {
    vi.useFakeTimers()
    try {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, sessionResponse()))
      vi.stubGlobal('fetch', fetchMock)

      loadWidget()
      const launcher = document.querySelector<HTMLButtonElement>('.zhihub-widget-launcher')!
      launcher.click()
      const input = document.querySelector<HTMLInputElement>('.zhihub-widget-box input')!
      input.value = '你好'
      document.querySelector<HTMLFormElement>('.zhihub-widget-box form')!.dispatchEvent(new Event('submit', { cancelable: true }))
      await vi.runAllTimersAsync()

      expect(FakeWebSocket.instances.length).toBe(1)
      const sock = FakeWebSocket.instances[0]
      // 断线（真实浏览器 onclose 后 readyState=3）→ 触发重连调度
      sock.readyState = 3
      sock.onclose && sock.onclose({ code: 1006 })
      await vi.runAllTimersAsync()
      expect(FakeWebSocket.instances.length).toBe(2) // 已发生一次重连

      // 再次断线，让重连定时器挂起，然后 destroy
      const sock2 = FakeWebSocket.instances[1]
      sock2.readyState = 3
      sock2.onclose && sock2.onclose({ code: 1006 })
      await vi.runOnlyPendingTimersAsync() // 让 onclose 的 scheduleReconnect 定时器挂起（不执行重连）
      const instancesBefore = FakeWebSocket.instances.length
      ;(window as any).ZhiHubWidget.destroy()
      expect(sock2.closed).toBe(true) // 旧连接被关闭
      await vi.runAllTimersAsync() // 时间前进越过重连延迟
      expect(FakeWebSocket.instances.length).toBe(instancesBefore) // 不再重连
      expect(document.querySelector('#zhihub-widget-root')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})
