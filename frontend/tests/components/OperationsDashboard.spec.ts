import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/analytics', () => ({ getAnalyticsOverview: vi.fn() }))
vi.mock('element-plus', async (original) => {
  const actual = await original<typeof import('element-plus')>()
  return { ...actual, ElMessage: { error: vi.fn() } }
})

import { getAnalyticsOverview } from '@/api/analytics'
import OperationsDashboard from '@/views/analytics/OperationsDashboard.vue'

const overview = {
  scope: 'tenant', days: 30, generated_at: '2026-08-24T10:00:00Z', event_store_available: true,
  summary: {
    event_count: 8, auto_resolution_rate: 75, average_csat: 4.6,
    mcp_success_rate: 98, approval_latency_seconds: 1800, security_blocks: 1,
  },
  retrieval_comparison: [
    { mode: 'vector', label: '纯向量', executions: 10, avg_score: 0.51, avg_latency_ms: 45 },
    { mode: 'hybrid', label: '混合检索', executions: 10, avg_score: 0.82, avg_latency_ms: 67 },
    { mode: 'hyde', label: 'HyDE 兜底', executions: 2, avg_score: 0.62, avg_latency_ms: 91 },
  ],
  resolution_trend: [
    { date: '2026-08-23', auto: 3, human: 1, auto_rate: 75 },
    { date: '2026-08-24', auto: 4, human: 1, auto_rate: 80 },
  ],
  approval_latency_distribution: [
    { label: '1 小时内', count: 3 }, { label: '1–4 小时', count: 1 },
    { label: '4–24 小时', count: 0 }, { label: '24 小时以上', count: 0 },
  ],
  plan_distribution: [{ plan: 'pro', count: 1 }],
  revenue_trend: [{ period: '2026-08', revenue: 909 }],
  recent_events: [{
    event_name: 'retrieval_executed', tenant_id: 1, occurred_at: '2026-08-24T09:00:00Z',
    properties: { confidence: 0.82, latency: 67 },
  }],
}

beforeEach(() => {
  vi.clearAllMocks()
  ;(getAnalyticsOverview as any).mockResolvedValue({ data: overview })
})

describe('OperationsDashboard', () => {
  it('renders all five requested analytics areas', async () => {
    const wrapper = mount(OperationsDashboard, { global: { plugins: [ElementPlus] } })
    await flushPromises()

    expect(getAnalyticsOverview).toHaveBeenCalledWith(30)
    expect(wrapper.text()).toContain('检索效果对照')
    expect(wrapper.text()).toContain('自动解决走势')
    expect(wrapper.text()).toContain('退款审批时效')
    expect(wrapper.text()).toContain('回款趋势')
    expect(wrapper.text()).toContain('套餐分布')
    expect(wrapper.text()).toContain('0.82')
    expect(wrapper.text()).toContain('¥909')
  })

  it('shows a clear degraded message when Mongo is unavailable', async () => {
    ;(getAnalyticsOverview as any).mockResolvedValue({
      data: { ...overview, event_store_available: false },
    })
    const wrapper = mount(OperationsDashboard, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('事件库暂时不可用')
    expect(wrapper.text()).toContain('财务与套餐数据仍可查看')
  })
})
