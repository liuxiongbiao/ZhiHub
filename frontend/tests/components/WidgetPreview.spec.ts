/**
 * tests/components/WidgetPreview.spec.ts
 *
 *  目标四: WidgetPreview 双模式闭环
 * - 保留"后台测试台"（登录态 /session 接口）并明确标注，避免假闭环
 * - 新增"真实 Widget 通道预览": 粘贴 API Key 后注入真实 /widget.js（公共匿名通道）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

const { mocks, msg } = vi.hoisted(() => ({
  mocks: {
    createSession: vi.fn(),
    createTicket: vi.fn(),
    getWidgetEmbedCode: vi.fn(),
    getWidgetConfig: vi.fn(),
  },
  msg: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/api/session', () => ({ createSession: mocks.createSession }))
vi.mock('@/api/ticket', () => ({ createTicket: mocks.createTicket }))
vi.mock('@/api/tenant', () => ({
  getWidgetEmbedCode: mocks.getWidgetEmbedCode,
  getWidgetConfig: mocks.getWidgetConfig,
}))
vi.mock('element-plus', async (o) => {
  const a = await o<typeof import('element-plus')>()
  return { ...a, ElMessage: msg }
})

import WidgetPreview from '@/views/widget-demo/WidgetPreview.vue'

const EMBED_RESPONSE = {
  data: {
    embed_code: '<script src="https://chat.example.com/widget.js" data-api-key="PASTE_WIDGET_API_KEY" data-endpoint="https://chat.example.com/api/v1/widget" data-ws-endpoint="wss://chat.example.com/api/v1/ws" data-theme-color="#1890ff" async></script>',
  },
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  document.querySelectorAll('script[data-api-key]').forEach((el) => el.remove())
  mocks.getWidgetEmbedCode.mockResolvedValue(EMBED_RESPONSE)
  mocks.getWidgetConfig.mockResolvedValue({
    data: { tenant_id: 1, theme_color: '#123456', logo_url: 'https://cdn.example.com/logo.png', welcome_text: '欢迎', domains: [] },
  })
  delete (window as any).ZhiHubWidget
})

/** 打开真实通道 tab 并注入 script 元素 */
async function injectScript(w: ReturnType<typeof mountPage>) {
  await switchToRealTab(w)
  await w.find('input[data-test="real-api-key-input"]').setValue('zh-real-key-123')
  await w.find('[data-test="load-real-widget-btn"]').trigger('click')
  await flushPromises()
  return document.querySelector<HTMLScriptElement>('script[data-api-key]')
}

function mountPage() {
  return mount(WidgetPreview, { global: { plugins: [ElementPlus] } })
}

async function switchToRealTab(w: ReturnType<typeof mountPage>) {
  const tab = w.findAll('.el-tabs__item').find((i) => i.text().includes('真实 Widget 通道'))
  await tab!.trigger('click')
  await flushPromises()
}

describe('WidgetPreview ( 目标四)', () => {
  it('keeps the backend test bench clearly labelled (logged-in /session channel)', () => {
    const w = mountPage()
    expect(w.text()).toContain('后台测试台')
    expect(w.text()).toContain('开始对话')
  })

  it('offers the real widget channel preview section', () => {
    const w = mountPage()
    expect(w.find('[data-test="real-widget-section"]').exists()).toBe(true)
    expect(w.find('[data-test="real-api-key-input"]').exists()).toBe(true)
    expect(w.find('[data-test="load-real-widget-btn"]').exists()).toBe(true)
  })

  it('injects the real widget.js script with api key when clicked', async () => {
    const w = mountPage()
    const injected = await injectScript(w)
    expect(injected).toBeTruthy()
    expect(injected!.getAttribute('data-api-key')).toBe('zh-real-key-123')
    expect(injected!.getAttribute('src')).toContain('/widget.js')
    expect(injected!.getAttribute('data-endpoint')).toBe('https://chat.example.com/api/v1/widget')
    expect(injected!.getAttribute('data-ws-endpoint')).toBe('wss://chat.example.com/api/v1/ws')
  })

  it('shows real tenant theme and logo in the real channel section', async () => {
    const w = mountPage()
    await flushPromises()
    await switchToRealTab(w)
    // 租户真实主题色与 Logo 展示（来自 getWidgetConfig，非固定默认样式）
    expect(w.find('[data-test="tenant-theme-swatch"]').exists()).toBe(true)
    const swatch = w.find('[data-test="tenant-theme-swatch"]').attributes('style') || ''
    // jsdom 将 hex 归一化为 rgb
    expect(swatch).toContain('rgb(18, 52, 86)')
    expect(w.text()).toContain('#123456')
    const logo = w.find('[data-test="tenant-logo-img"]')
    expect(logo.exists()).toBe(true)
    expect(logo.attributes('src')).toBe('https://cdn.example.com/logo.png')
  })

  it('injects the widget with the tenant theme color attribute', async () => {
    const w = mountPage()
    const injected = await injectScript(w)
    expect(injected!.getAttribute('data-theme-color')).toBe('#123456')
  })
})

describe('WidgetPreview script lifecycle ()', () => {
  it('keeps loading until script.onload, then marks success', async () => {
    const w = mountPage()
    const injected = await injectScript(w)
    // onload 前: 不显示成功提示
    expect(w.text()).not.toContain('已注入真实 widget.js')
    injected!.onload?.(new Event('load'))
    await flushPromises()
    expect(w.text()).toContain('已注入真实 widget.js')
    expect(msg.success).toHaveBeenCalled()
  })

  it('onerror cleans up the failed script and shows error', async () => {
    const w = mountPage()
    const injected = await injectScript(w)
    injected!.onerror?.(new Event('error'))
    await flushPromises()
    expect(document.querySelector('script[data-api-key]')).toBeNull() // 失败脚本被移除
    expect(w.text()).not.toContain('已注入真实 widget.js')
    expect(msg.error).toHaveBeenCalled()
  })

  it('reloading destroys the old widget instance via ZhiHubWidget.destroy()', async () => {
    const destroySpy = vi.fn()
    ;(window as any).ZhiHubWidget = { destroy: destroySpy }
    const w = mountPage()
    await injectScript(w)
    await injectScript(w)
    expect(destroySpy).toHaveBeenCalled()
    // 只有一份 script（旧的被移除）
    expect(document.querySelectorAll('script[data-api-key]').length).toBe(1)
  })

  it('reloading without destroy API still removes stale DOM', async () => {
    document.body.insertAdjacentHTML('beforeend', '<div id="zhihub-widget-root"></div>')
    const w = mountPage()
    await injectScript(w)
    expect(document.querySelector('#zhihub-widget-root')).toBeNull()
  })

  it('does not persist the pasted api key', async () => {
    mocks.getWidgetEmbedCode.mockResolvedValue({
      data: { embed_code: '<script src="https://chat.example.com/widget.js" data-api-key="PASTE_WIDGET_API_KEY" data-endpoint="https://chat.example.com/api/v1/widget" data-ws-endpoint="wss://chat.example.com/api/v1/ws" async></script>' },
    })
    const w = mountPage()
    await flushPromises()
    await switchToRealTab(w)
    await w.find('input[data-test="real-api-key-input"]').setValue('zh-secret-preview-key')
    await w.find('[data-test="load-real-widget-btn"]').trigger('click')
    await flushPromises()
    expect(JSON.stringify(localStorage)).not.toContain('zh-secret-preview-key')
  })
})
