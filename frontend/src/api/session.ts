// frontend/src/api/session.ts
// 职责: 会话/消息 API 接口层
// 013 US3/US4/US5: Trace / 留言建单 / CSAT / 快捷回复

import request from './request'
import type { Session, SessionDetail, SendMessageResponse } from '@/types/session'
import type { TraceResponse, LeaveMessageResponse, CSATResponse, QuickReplyItem } from '@/types/session'

/** 创建会话 */
export function createSession(initialMessage?: string) {
  return request.post<{ session_id: number; status: string; created_at: string }>('/session', {
    initial_message: initialMessage || '',
  })
}

/** 获取会话列表 */
export function getSessionList(params: { page?: number; page_size?: number; status?: string }) {
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

/** 坐席认领会话（Day 6） */
export function claimSession(sessionId: number) {
  return request.post<{ session_id: number; agent_id: number; status: string }>(`/session/${sessionId}/claim`)
}

/** 坐席退回会给AI（013 US3，双向协作） */
export function returnToAgent(sessionId: number) {
  return request.post<{ session_id: number; status: string }>(`/session/${sessionId}/return-to-agent`)
}

/** 获取 Agent 执行轨迹（013 US3，坐席工作台 Trace） */
export function getTrace(sessionId: number) {
  return request.get<TraceResponse>(`/session/${sessionId}/trace`)
}

/** 无坐席留言自动建单（013 US4） */
export function leaveMessage(sessionId: number, content: string) {
  return request.post<LeaveMessageResponse>(`/session/${sessionId}/leave-message`, { content })
}

/** 提交 CSAT 满意度评价（013 US4） */
export function submitCsat(sessionId: number, score: number, comment?: string) {
  return request.post<CSATResponse>(`/session/${sessionId}/csat`, { score, comment })
}

/** 快捷回复列表（013 US5） */
export function getQuickReplies() {
  return request.get<QuickReplyItem[]>('/session/quick-replies')
}

/** 新增快捷回复（013 US5） */
export function createQuickReply(content: string, shortcut?: string) {
  return request.post<QuickReplyItem>('/session/quick-replies', { content, shortcut })
}


// ---- 013 US7: SSE 流式响应 ----

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
  const raw = localStorage.getItem('zhihub:user')
  const token = raw ? (JSON.parse(raw).token ?? '') : ''
  const resp = await fetch('/api/v1/session/message/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ session_id: sessionId, content }),
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`流式请求失败: ${resp.status}`)
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
