/**
 * tests/components/WidgetConfig.spec.ts
 *
 *  目标一: WidgetConfig 页面接入 API Key 轮换 + 域名白名单 + 品牌配置 + 嵌入代码。
 * - 挂载时加载完整配置（品牌 + 域名）
 * - 轮换 Key: 只在生成时显示一次、提供复制、明确提示旧 Key 立即失效
 * - Key 不落 localStorage
 * - 域名白名单保存调用 setWidgetDomains
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

const { mocks } = vi.hoisted(() => ({
  mocks: {
    getWidgetConfig: vi.fn(),
    updateWidgetConfig: vi.fn(),
    rotateWidgetApiKey: vi.fn(),
    getWidgetDomains: vi.fn(),
    setWidgetDomains: vi.fn(),
    getWidgetEmbedCode: vi.fn(),
  }
}))
vi.mock('@/api/tenant', () => mocks)
vi.mock('element-plus', async (o) => {
  const a = await o<typeof import('element-plus')>()
  return { ...a, ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }
})

import WidgetConfig from '@/views/onboarding/WidgetConfig.vue'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.clearAllMocks()
  mocks.getWidgetConfig.mockResolvedValue({
    data: {
      tenant_id: 1, theme_color: '#ff0000', logo_url: 'https://cdn.example.com/l.png',
      welcome_text: '你好', domains: ['a.example.com', 'b.example.com'],
    },
  })
})

function mountPage() {
  return mount(WidgetConfig, { global: { plugins: [ElementPlus] } })
}

describe('WidgetConfig ()', () => {
  it('loads brand config and domain whitelist on mount', async () => {
    const w = mountPage()
    await flushPromises()
    expect(mocks.getWidgetConfig).toHaveBeenCalled()
    // 域名白名单以逗号分隔预填（textarea value 不出现在 text() 里，读元素值）
    const domainInput = w.find('textarea').element as HTMLTextAreaElement
    expect(domainInput.value).toBe('a.example.com, b.example.com')
  })

  it('rotating api key shows it exactly once with copy entry and rotation warning', async () => {
    mocks.rotateWidgetApiKey.mockResolvedValue({ data: { api_key: 'zh-new-secret-key' } })
    const w = mountPage()
    await flushPromises()
    await w.find('[data-test="rotate-key-btn"]').trigger('click')
    await flushPromises()
    expect(mocks.rotateWidgetApiKey).toHaveBeenCalledTimes(1)
    // 生成后页面显示 Key（一次性）与复制按钮，并提示旧 Key 立即失效
    expect(w.text()).toContain('zh-new-secret-key')
    expect(w.find('[data-test="copy-key-btn"]').exists()).toBe(true)
    expect(w.text()).toContain('旧 Key 立即失效')
  })

  it('never persists api key to localStorage', async () => {
    mocks.rotateWidgetApiKey.mockResolvedValue({ data: { api_key: 'zh-secret-not-stored' } })
    const w = mountPage()
    await flushPromises()
    await w.find('[data-test="rotate-key-btn"]').trigger('click')
    await flushPromises()
    const stored = JSON.stringify(localStorage)
    expect(stored).not.toContain('zh-secret-not-stored')
  })

  it('saving domains calls setWidgetDomains with parsed list', async () => {
    mocks.setWidgetDomains.mockResolvedValue({ data: { domains: ['x.example.com'] } })
    const w = mountPage()
    await flushPromises()
    await w.find('[data-test="save-domains-btn"]').trigger('click')
    await flushPromises()
    expect(mocks.setWidgetDomains).toHaveBeenCalledWith(['a.example.com', 'b.example.com'])
  })

  it('saving brand config calls updateWidgetConfig', async () => {
    mocks.updateWidgetConfig.mockResolvedValue({ data: {} })
    mocks.getWidgetEmbedCode.mockResolvedValue({ data: { embed_code: '<script>' } })
    const w = mountPage()
    await flushPromises()
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(mocks.updateWidgetConfig).toHaveBeenCalledWith(expect.objectContaining({
      theme_color: '#ff0000', welcome_text: '你好',
    }))
  })

  it('clearing the logo input and saving submits empty string (explicit clear)', async () => {
    mocks.updateWidgetConfig.mockResolvedValue({ data: {} })
    mocks.getWidgetEmbedCode.mockResolvedValue({ data: { embed_code: '<script>' } })
    mocks.getWidgetConfig.mockResolvedValue({
      data: { tenant_id: 1, theme_color: '#ff0000', logo_url: 'https://cdn.example.com/logo.png', welcome_text: '你好', domains: [] },
    })
    const w = mountPage()
    await flushPromises()
    // 清空 Logo 输入框
    await w.find('input[placeholder="https://example.com/logo.png"]').setValue('')
    await w.find('form').trigger('submit')
    await flushPromises()
    // 明确提交空值（区别于不提交字段）
    expect(mocks.updateWidgetConfig).toHaveBeenCalledWith(expect.objectContaining({ logo_url: '' }))
  })

  it('embed code button loads and displays the snippet', async () => {
    mocks.getWidgetEmbedCode.mockResolvedValue({ data: { embed_code: '<script src="https://chat.example.com/widget.js"></script>' } })
    const w = mountPage()
    await flushPromises()
    await w.find('[data-test="load-embed-btn"]').trigger('click')
    await flushPromises()
    expect(mocks.getWidgetEmbedCode).toHaveBeenCalled()
    expect(w.text()).toContain('chat.example.com/widget.js')
  })
})
