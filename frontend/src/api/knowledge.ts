// frontend/src/api/knowledge.ts
// 职责: 知识库文档上传/列表/重建索引/检索测试接口

import request from './request'

export interface DocumentItem {
  doc_id: number
  filename: string
  file_type: string
  file_size: number
  status: string
  version: number
  is_latest: boolean
  chunk_count: number
  uploaded_at: string
  expires_at?: string | null
  quality_status?: string
}

export interface DocumentListResponse {
  items: DocumentItem[]
  total: number
  page: number
  page_size: number
}

export interface ChunkResult {
  doc_id: number
  doc_filename: string
  chunk_index: number
  chunk_text: string
  score: number
  source: string
}

export interface SearchTestResponse {
  query: string
  rewritten_query: string | null
  vector_results: ChunkResult[]
  hybrid_results: ChunkResult[]
  hyde_fallback: boolean
  empty_knowledge: boolean
  elapsed_ms: number
}

export interface RebuildResponse {
  doc_id: number
  status: string
  message: string
}

/**
 * 上传文档
 * @param expiresDays 文档有效期天数：缺省=系统默认90天，0=永久（PRD §3.2 生命周期治理）
 */
export function uploadDocument(file: File, expiresDays?: number) {
  const formData = new FormData()
  formData.append('file', file)
  if (expiresDays !== undefined) {
    formData.append('expires_days', String(expiresDays))
  }
  return request.post<DocumentItem>('/knowledge/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/**
 * 获取文档列表
 */
export function getDocumentList(params: {
  status?: string
  page?: number
  page_size?: number
}) {
  return request.get<DocumentListResponse>('/knowledge/list', { params })
}

/**
 * 重建索引
 */
export function rebuildDocument(docId: number) {
  return request.post<RebuildResponse>(`/knowledge/${docId}/rebuild`)
}

/** 删除文档（失败/过期文档清理，释放套餐配额） */
export function deleteDocument(docId: number) {
  return request.delete<{ doc_id: number; deleted: boolean }>(`/knowledge/${docId}`)
}

/**
 * 检索测试
 */
export function searchTest(query: string, history: Array<{ role: string; content: string }> = []) {
  return request.post<SearchTestResponse>('/knowledge/test', { query, history })
}
