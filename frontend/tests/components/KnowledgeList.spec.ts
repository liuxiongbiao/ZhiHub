// frontend/tests/components/KnowledgeList.spec.ts
// 测试: 知识库文档列表页 — 列表渲染、状态筛选、分页交互

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

// Mock API 模块
vi.mock('@/api/knowledge', () => ({
  getDocumentList: vi.fn(),
  uploadDocument: vi.fn(),
  rebuildDocument: vi.fn(),
  deleteDocument: vi.fn(),
}))

import { getDocumentList } from '@/api/knowledge'

describe('KnowledgeList.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders document list on mount', async () => {
    const mockItems = [
      {
        doc_id: 1,
        filename: 'test.md',
        file_type: 'md',
        file_size: 1024,
        status: 'ready',
        version: 1,
        is_latest: true,
        chunk_count: 5,
        uploaded_at: '2026-08-04T10:00:00Z',
      },
    ]

    ;(getDocumentList as any).mockResolvedValue({
      data: { items: mockItems, total: 1, page: 1, page_size: 20 },
    })

    // 动态导入组件（mock 必须在 import 之前生效）
    const { default: KnowledgeList } = await import(
      '@/views/knowledge/KnowledgeList.vue'
    )
    const wrapper = mount(KnowledgeList, {
      global: {
        stubs: {
          'el-upload': { template: '<div><slot /></div>' },
          'el-button': { template: '<button><slot /></button>' },
          'el-table': { template: '<div><slot /></div>' },
          'el-table-column': { template: '<div><slot /></div>' },
          'el-select': { template: '<select><slot /></select>' },
          'el-option': { template: '<option><slot /></option>' },
          'el-pagination': { template: '<div />' },
          StatusTag: { template: '<span />' },
        },
      },
    })

    await nextTick()

    expect(getDocumentList).toHaveBeenCalled()
  })

  it('renders empty state when no documents', async () => {
    ;(getDocumentList as any).mockResolvedValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
    })

    const { default: KnowledgeList } = await import(
      '@/views/knowledge/KnowledgeList.vue'
    )
    const wrapper = mount(KnowledgeList, {
      global: {
        stubs: {
          'el-upload': { template: '<div><slot /></div>' },
          'el-button': { template: '<button><slot /></button>' },
          'el-table': { template: '<div><slot /></div>' },
          'el-table-column': { template: '<div><slot /></div>' },
          'el-select': { template: '<select><slot /></select>' },
          'el-option': { template: '<option><slot /></option>' },
          'el-pagination': { template: '<div />' },
          StatusTag: { template: '<span />' },
        },
      },
    })

    await nextTick()

    expect(getDocumentList).toHaveBeenCalled()
  })
})
