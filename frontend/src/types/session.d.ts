// frontend/src/types/session.d.ts
// 职责: 会话/消息/状态枚举/情绪枚举类型（对齐 backend/app/models/session.py）

/** 会话状态 */
export type SessionStatus =
  | 'created'
  | 'routing'
  | 'agent_handling'
  | 'waiting_human'
  | 'human_handling'
  | 'resolved'
  | 'closed'

/** 意图类型 */
export type IntentType =
  | 'knowledge_consult'
  | 'ticket_query'
  | 'order_query'
  | 'sensitive_action'

/** 情绪标签 */
export type SentimentType = 'normal' | 'unhappy' | 'angry'

/** 消息角色 */
export type MessageRole = 'user' | 'assistant' | 'agent' | 'system'

/** 引用来源 */
export interface SourceItem {
  doc: string
  chunk_index: number
  text: string
}

/** 消息 */
export interface Message {
  message_id: number
  role: MessageRole
  content: string
  sources?: SourceItem[]
  created_at: string
}

/** 会话 */
export interface Session {
  session_id: number
  status: SessionStatus
  intent?: IntentType
  sentiment?: SentimentType
  message_count: number
  created_at: string
  updated_at: string
}

/** 会话详情（含消息列表） */
export interface SessionDetail extends Session {
  messages: Message[]
}

/** 发送消息响应 */
export interface SendMessageResponse {
  message_id: number
  role: string
  content: string
  sources: SourceItem[]
  metadata?: {
    intent?: string
    confidence?: number
    sentiment?: string
    elapsed_ms?: number
  }
}


// 013 US3/US4: Trace / 留言 / CSAT / 快捷回复 类型（对齐后端 schemas/session.py）
export interface TraceResponse {
  session_id: number
  traces: AgentTrace[]
  total: number
}

export interface LeaveMessageResponse {
  ticket_no: string
  session_id: number
}

export interface CSATResponse {
  id: number
  session_id: number
  score: number
}

export interface QuickReplyItem {
  id: number
  content: string
  shortcut?: string | null
}
