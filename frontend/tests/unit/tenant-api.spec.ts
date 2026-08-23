/**
 * tests/unit/tenant-api.spec.ts
 * 规格 013 US5 (T034): inviteMember 调用契约
 * : Widget 配置/Key 轮换/域名白名单 API 契约
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
const { mockRequest } = vi.hoisted(() => ({
  mockRequest: { get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))
vi.mock('@/api/request', () => ({ default: mockRequest }))
import {
  inviteMember, getAuditLogs,
  getWidgetConfig, rotateWidgetApiKey, getWidgetDomains, setWidgetDomains,
  getPlanCatalog, changeTenantPlan,
} from '@/api/tenant'
beforeEach(() => { vi.clearAllMocks() })
describe('tenant api (规格 013 US5)', () => {
  it('inviteMember posts to /tenant/invite with phone', async () => {
    mockRequest.post.mockResolvedValue({ data: { invite_url: 'http://x' } })
    await inviteMember('13900139000')
    expect(mockRequest.post).toHaveBeenCalledWith('/tenant/invite', { phone: '13900139000' })
  })
  it('getAuditLogs gets /tenant/audit-logs', async () => {
    mockRequest.get.mockResolvedValue({ data: { items: [], total: 0 } })
    await getAuditLogs(1, 20)
    expect(mockRequest.get).toHaveBeenCalledWith('/tenant/audit-logs', { params: { page: 1, page_size: 20 } })
  })
})

describe('tenant widget api ()', () => {
  it('getWidgetConfig gets /tenant/widget', async () => {
    mockRequest.get.mockResolvedValue({ data: { tenant_id: 1, theme_color: '#123456', domains: ['a.com'] } })
    await getWidgetConfig()
    expect(mockRequest.get).toHaveBeenCalledWith('/tenant/widget')
  })

  it('rotateWidgetApiKey posts to /tenant/widget/api-key', async () => {
    mockRequest.post.mockResolvedValue({ data: { api_key: 'new-key' } })
    await rotateWidgetApiKey()
    expect(mockRequest.post).toHaveBeenCalledWith('/tenant/widget/api-key')
  })

  it('getWidgetDomains gets /tenant/widget/domains', async () => {
    mockRequest.get.mockResolvedValue({ data: { domains: ['a.com'] } })
    await getWidgetDomains()
    expect(mockRequest.get).toHaveBeenCalledWith('/tenant/widget/domains')
  })

  it('setWidgetDomains puts /tenant/widget/domains with domain list', async () => {
    mockRequest.put.mockResolvedValue({ data: { domains: ['a.com', 'b.com'] } })
    await setWidgetDomains(['a.com', 'b.com'])
    expect(mockRequest.put).toHaveBeenCalledWith('/tenant/widget/domains', { domains: ['a.com', 'b.com'] })
  })
})

describe('tenant plan api (PRD 3.9)', () => {
  it('gets plan catalog', async () => {
    mockRequest.get.mockResolvedValue({ data: { current_plan: 'trial', plans: [] } })
    await getPlanCatalog()
    expect(mockRequest.get).toHaveBeenCalledWith('/tenant/plans')
  })

  it('puts requested target plan', async () => {
    mockRequest.put.mockResolvedValue({ data: { current_plan: 'basic', change_status: 'effective' } })
    await changeTenantPlan('basic')
    expect(mockRequest.put).toHaveBeenCalledWith('/tenant/plan', { target_plan: 'basic' })
  })
})
