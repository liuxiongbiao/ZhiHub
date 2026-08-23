// frontend/src/api/session.ts
// 职责: 会话/消息 API 接口层
// 规格 013 US3/US4/US5: Trace / 留言建单 / CSAT / 快捷回复

import request from './request'
import { getAuthToken, handleAuthFailure } from './auth-session'
import type { Session, SessionDetail, SendMessageResponse } from '@/types/session'
import type { TraceResponse, LeaveMessageResponse, CSATResponse, QuickReplyItem } from '@/types/session'
import type { AgentTrace } from '@/types/trace'

/** 创建会话 */
export function createSession(initialMessage?: string) {
  return request.post<{ session_id: number; status: string; created_at: string }>('/session', {
    initial_message: initialMessage || '',
  })
}

/** 获取会话列表 */
export function getSessionList(params: {
  page?: number; page_size?: number; status?: string; sentiment?: string
}) {
  return request.get<{ items: Session[]; total: number; page: number; page_size: number }>(
    '/session/list', { params }
  )
}

/** 获取会话详情 */
export function getSessionDetail(sessionId: number) {
  return request.get<SessionDetail>(`/session/${sessionId}`)
}

/** 发送消息 */
export function sendMessage(sessionId: number, content: string) {
  return request.post<SendMessageResponse>('/session/message', { session_id: sessionId, content })
}

/** 当前认领客服回复人工接管会话，不进入智能体链路。 */
export function sendAgentMessage(sessionId: number, content: string) {
  return request.post<SendMessageResponse>(`/session/${sessionId}/agent-message`, { content })
}

export function claimSession(sessionId: number) {
  return request.post<{ session_id: number; agent_id: number; status: string }>(`/session/${sessionId}/claim`)
}

/** 坐席退回会给AI（规格 013 US3，双向协作） */
export function returnToAgent(sessionId: number) {
  return request.post<{ session_id: number; status: string }>(`/session/${sessionId}/return-to-agent`)
}

/** 获取 Agent 执行轨迹；后端按 created_at DESC 返回，traces[0] 为最新。 */
export function getTrace(sessionId: number) {
  return request.get<TraceResponse>(`/session/${sessionId}/trace`)
}

export function selectLatestTrace(traces: AgentTrace[] | undefined): AgentTrace | null {
  return traces && traces.length > 0 ? traces[0] : null
}

/** 无坐席留言自动建单（规格 013 US4） */
export function leaveMessage(sessionId: number, content: string) {
  return request.post<LeaveMessageResponse>(`/session/${sessionId}/leave-message`, { content })
}

/** 提交 CSAT 满意度评价（规格 013 US4） */
export function submitCsat(sessionId: number, score: number, comment?: string) {
  return request.post<CSATResponse>(`/session/${sessionId}/csat`, { score, comment })
}

/** 快捷回复列表（规格 013 US5） */
export function getQuickReplies() {
  return request.get<QuickReplyItem[]>('/session/quick-replies')
}

/** 新增快捷回复（规格 013 US5） */
export function createQuickReply(content: string, shortcut?: string) {
  return request.post<QuickReplyItem>('/session/quick-replies', { content, shortcut })
}


/** 提交回复反馈（PRD §§3.8） */
export function submitFeedback(messageId: number, score: number, comment?: string) {
  return request.post<{ id: number; message_id: number; score: number; bad_case: boolean }>(
    `/session/message/${messageId}/feedback`, { score, comment }
  )
}

// ———— 规格 013 US7: SSE 流式响应 ————

/**
 * 流式发送消息：POST /session/message/stream，逐字回调 onChunk，[DONE] 结束。
 * 用 fetch + ReadableStream 解析 SSE（非 EventSource，因需 POST）。
 */
export async function sendMessageStream(
  sessionId: number,
  content: string,
  onChunk: (chunk: string) => void,
  onMeta?: (messageId: number) => void,
): Promise<void> {
  const token = getAuthToken()
  const resp = await fetch('/api/v1/session/message/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      'X-CSRF-Token': token,
    },
    body: JSON.stringify({ session_id: sessionId, content }),
  })
  if (!resp.ok || !resp.body) {
    let message = `流式请求失败: ${resp.status}`
    try {
      const data = await resp.json() as { detail?: string }
      if (data.detail) message = data.detail
    } catch { /* 非 JSON 错误响应沿用状态码 */ }
    if (resp.status === 401 || resp.status === 403) handleAuthFailure()
    throw new StreamRequestError(resp.status, message)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6)
      if (payload === '[DONE]') return
      try {
        const obj = JSON.parse(payload)
        if (obj.chunk) onChunk(obj.chunk)
        else if (obj.message_id && onMeta) onMeta(obj.message_id)
        else if (obj.error) throw new Error(obj.error)
      } catch (e) {
        if (e instanceof SyntaxError) continue
        throw e
      }
    }
  }
}

export class StreamRequestError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'StreamRequestError'
  }
}
