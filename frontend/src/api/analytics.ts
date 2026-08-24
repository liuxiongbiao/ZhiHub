import request from './request'

export interface AnalyticsSummary {
  event_count: number
  auto_resolution_rate: number
  average_csat: number
  mcp_success_rate: number
  approval_latency_seconds: number
  security_blocks: number
  retrieval_p95_ms: number
  active_sessions: number
  waiting_human: number
  pending_refunds: number
  knowledge_issues: number
  quota_used: number
  quota_total: number
  quota_usage_percent: number
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
  attention_items: Array<{
    kind: string
    title: string
    count: number
    path: string
    severity: 'high' | 'medium' | 'low'
  }>
}

export interface KnowledgeQualityOverview {
  generated_at: string
  days: number
  event_store_available: boolean
  quality_score: number
  documents: {
    total: number
    ready: number
    processing: number
    failed: number
    pending_review: number
    expired: number
    chunks: number
  }
  retrieval: {
    executions: number
    average_confidence: number
    p95_latency_ms: number
    low_confidence_count: number
  }
  recommendations: Array<{ severity: string; title: string; path: string }>
  missed_queries: Array<{ query: string; confidence: number; occurred_at: string }>
}

export function getAnalyticsOverview(days = 30) {
  return request.get<AnalyticsOverview>('/analytics/overview', { params: { days } })
}

export function getKnowledgeQuality(days = 30) {
  return request.get<KnowledgeQualityOverview>('/analytics/knowledge-quality', { params: { days } })
}

export function getPlatformAnalyticsOverview(days: number, platformToken: string) {
  return request.get<AnalyticsOverview>('/analytics/platform/overview', {
    params: { days },
    headers: { 'X-Platform-Token': platformToken },
  })
}
