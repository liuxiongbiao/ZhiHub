// frontend/src/api/knowledge.ts
// 职责: 知识库文档上传/列表/重建索引/检索测试接口
// 分工: 🟢 弱模型可独立完成
// 对应日程: Day 3

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
 */
export function uploadDocument(file: File) {
  const formData = new FormData()
  formData.append('file', file)
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

/**
 * 检索测试
 */
export function searchTest(query: string, history: Array<{ role: string; content: string }> = []) {
  return request.post<SearchTestResponse>('/knowledge/test', { query, history })
}
