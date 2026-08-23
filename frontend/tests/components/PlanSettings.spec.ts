/**
 * tests/components/PlanSettings.spec.ts
 *
 * 职责: 验证套餐卡片展示、升级确认与企业版待议价页面流程（PRD §3.9）。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  getPlanCatalog: vi.fn(),
  changeTenantPlan: vi.fn(),
  getRefundThreshold: vi.fn(),
  updateRefundThreshold: vi.fn(),
  exportTenantData: vi.fn(),
  getTenantCancellation: vi.fn(),
  requestTenantCancellation: vi.fn(),
  revokeTenantCancellation: vi.fn(),
}))
const confirm = vi.hoisted(() => vi.fn().mockResolvedValue('confirm'))

vi.mock('@/api/tenant', () => api)
vi.mock('element-plus', async (original) => {
  const actual = await original<typeof import('element-plus')>()
  return {
    ...actual,
    ElMessage: { success: vi.fn(), error: vi.fn() },
    ElMessageBox: { confirm },
  }
})

import PlanSettings from '@/views/onboarding/PlanSettings.vue'

const plans = [
  { plan_type: 'trial', name: '试用版', monthly_price: 0, conversation_quota: 100, knowledge_base_limit: 1, storage_quota_mb: 500, seat_limit: 1, negotiable: false },
  { plan_type: 'basic', name: '基础版', monthly_price: 999, conversation_quota: 2000, knowledge_base_limit: 3, storage_quota_mb: 5120, seat_limit: 2, negotiable: false },
  { plan_type: 'pro', name: '专业版', monthly_price: 4999, conversation_quota: 10000, knowledge_base_limit: null, storage_quota_mb: 51200, seat_limit: 10, negotiable: false },
  { plan_type: 'enterprise', name: '企业版', monthly_price: null, conversation_quota: 100000, knowledge_base_limit: null, storage_quota_mb: null, seat_limit: 10, negotiable: true },
]

function mountPage() {
  return mount(PlanSettings, {
    global: {
      plugins: [createPinia()],
      stubs: {
        PageHeader: { template: '<header />' },
        'el-card': { template: '<section><slot name="header"/><slot/></section>' },
        'el-tag': { template: '<span><slot/></span>' },
        'el-button': { emits: ['click'], template: '<button :disabled="$attrs.disabled" @click="$emit(\'click\')"><slot/></button>' },
        'el-form': { template: '<form><slot/></form>' },
        'el-form-item': { template: '<div><slot/></div>' },
        'el-input-number': { template: '<input />' },
      },
      directives: { loading: () => undefined },
    },
  })
}

describe('PlanSettings.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    api.getPlanCatalog.mockResolvedValue({ data: { current_plan: 'trial', pending_plan: null, plans } })
    api.getRefundThreshold.mockResolvedValue({ data: { refund_approval_threshold: 1000 } })
    api.getTenantCancellation.mockResolvedValue({
      data: {
        tenant_id: 1, status: 'active', deletion_pending: false,
        requested_at: null, scheduled_deletion_at: null, days_remaining: null,
        can_revoke: false, export_available: true,
      },
    })
    api.changeTenantPlan.mockResolvedValue({
      data: { tenant_id: 1, current_plan: 'basic', pending_plan: null, change_status: 'effective', seat_limit: 2, conversation_quota: 2000 },
    })
  })

  it('renders all four plan cards', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.text()).toContain('试用版')
    expect(wrapper.text()).toContain('基础版')
    expect(wrapper.text()).toContain('专业版')
    expect(wrapper.text()).toContain('企业版')
  })

  it('confirms and submits a basic plan upgrade', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const button = wrapper.findAll('button').find((item) => item.text().includes('升级至基础版'))
    expect(button).toBeTruthy()
    await button!.trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalled()
    expect(api.changeTenantPlan).toHaveBeenCalledWith('basic')
  })

  it('submits cancellation and switches to export-only status', async () => {
    api.requestTenantCancellation.mockResolvedValue({
      data: {
        tenant_id: 1, status: 'pending_deletion', deletion_pending: true,
        requested_at: '2026-08-24T00:00:00Z', scheduled_deletion_at: '2026-09-23T00:00:00Z',
        days_remaining: 30, can_revoke: true, export_available: true,
      },
    })
    const wrapper = mountPage()
    await flushPromises()
    const button = wrapper.findAll('button').find((item) => item.text().includes('申请注销租户'))
    await button!.trigger('click')
    await flushPromises()
    expect(api.requestTenantCancellation).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('注销等待期')
    expect(wrapper.text()).toContain('撤销注销')
  })

  it('renders an existing pending deletion without loading writable settings', async () => {
    api.getTenantCancellation.mockResolvedValue({
      data: {
        tenant_id: 1, status: 'pending_deletion', deletion_pending: true,
        requested_at: '2026-08-24T00:00:00Z', scheduled_deletion_at: '2026-09-23T00:00:00Z',
        days_remaining: 30, can_revoke: true, export_available: true,
      },
    })
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.text()).toContain('预计剩余 30 天')
    expect(api.getPlanCatalog).not.toHaveBeenCalled()
    expect(api.getRefundThreshold).not.toHaveBeenCalled()
  })
})
