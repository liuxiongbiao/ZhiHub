// frontend/src/types/trace.d.ts
// 职责: Agent执行步骤/工具调用/检索结果类型（对齐 MongoDB trace 结构）
// 分工: 🟡 强模型定字段结构，翻译成TS类型弱模型可做
// 对应日程: Day 7

/** 单步执行记录 */
export interface TraceStep {
  step: 'router' | 'retrieval' | 'agent' | 'generation'
  label: string
  intent?: string
  confidence?: number
  sentiment?: string
  slots?: Record<string, string>
  query?: string
  result_count?: number
  top_scores?: number[]
  agent_type?: string
  answer_preview?: string
  sources_count?: number
  elapsed_ms?: number | null
}

/** 完整trace记录 */
export interface AgentTrace {
  _id: string
  session_id: number
  tenant_id: number
  message_id: number
  intent: string
  confidence: number
  sentiment: string
  steps: TraceStep[]
  total_elapsed_ms: number
  created_at: string
}
