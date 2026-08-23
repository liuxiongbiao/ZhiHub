import request from './request'

export interface AnalyticsSummary {
  event_count: number
  auto_resolution_rate: number
  average_csat: number
  mcp_success_rate: number
  approval_latency_seconds: number
  security_blocks: number
}

export interface RetrievalComparison {
  mode: string
  label: string
  executions: number
  avg_score: number
  avg_latency_ms: number
}

export interface ResolutionPoint {
  date: string
  auto: number
  human: number
  auto_rate: number
}

export interface AnalyticsOverview {
  scope: 'tenant' | 'platform'
  days: number
  generated_at: string
  event_store_available: boolean
  summary: AnalyticsSummary
  retrieval_comparison: RetrievalComparison[]
  resolution_trend: ResolutionPoint[]
  approval_latency_distribution: Array<{ label: string; count: number }>
  plan_distribution: Array<{ plan: string; count: number }>
  revenue_trend: Array<{ period: string; revenue: number }>
  recent_events: Array<{
    event_name: string
    tenant_id: number
    properties: Record<string, unknown>
    occurred_at: string
  }>
}

export function getAnalyticsOverview(days = 30) {
  return request.get<AnalyticsOverview>('/analytics/overview', { params: { days } })
}
