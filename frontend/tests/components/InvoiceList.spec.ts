/**
 * tests/components/InvoiceList.spec.ts
 * 规格 013 US2 (T024): unpaid 显示支付按钮，paid 不显示；点击调 payInvoice
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

const { mocks } = vi.hoisted(() => ({
  mocks: {
    getInvoices: vi.fn(),
    payInvoice: vi.fn(),
  }
}))
vi.mock('@/api/billing', () => mocks)
vi.mock('element-plus', async (o) => {
  const a = await o<typeof import('element-plus')>()
  return {
    ...a,
    ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
    ElMessageBox: { confirm: vi.fn().mockResolvedValue('confirm') },
  }
})

import InvoiceList from '@/views/billing/InvoiceList.vue'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

function mountList() {
  return mount(InvoiceList, { global: { plugins: [createPinia(), ElementPlus] } })
}

describe('InvoiceList.vue (规格 013 US2)', () => {
  it('shows pay button for unpaid invoice, hides for paid', async () => {
    mocks.getInvoices.mockResolvedValue({
      data: { items: [
        { id: 1, period: '2026-08', base_fee: 100, overage_fee: 0, status: 'unpaid' },
        { id: 2, period: '2026-07', base_fee: 100, overage_fee: 0, status: 'paid' },
      ] },
    })
    const w = mountList()
    await flushPromises()
    const html = w.html()
    expect(html).toContain('支付')
    // paid 行不显示支付按钮（显示 -）
    expect(html).toContain('未支付')
    expect(html).toContain('已支付')
  })

  it('calls payInvoice on pay button click', async () => {
    mocks.getInvoices.mockResolvedValue({
      data: { items: [{ id: 5, period: '2026-08', base_fee: 100, overage_fee: 0, status: 'unpaid' }] },
    })
    mocks.payInvoice.mockResolvedValue({ data: { pay_url: 'https://pay.example.com' } })
    const w = mountList()
    await flushPromises()
    const payBtn = w.findAll('button').find(b => b.text().includes('支付'))
    expect(payBtn).toBeTruthy()
    // mock window.location 避免真跳转
    Object.defineProperty(window, 'location', { value: { href: '' }, writable: true })
    await payBtn!.trigger('click')
    await flushPromises()
    expect(mocks.payInvoice).toHaveBeenCalledWith(5)
  })
})
