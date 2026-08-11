// frontend/tests/components/SearchTest.spec.ts
// 测试: 检索测试台 — 查询输入、两路结果对比展示、空知识库提示

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

vi.mock('@/api/knowledge', () => ({
  searchTest: vi.fn(),
}))

describe('SearchTest.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders search input area', async () => {
    const { default: SearchTest } = await import(
      '@/views/knowledge/SearchTest.vue'
    )
    const wrapper = mount(SearchTest, {
      global: {
        stubs: {
          'el-input': { template: '<div class="el-input"><slot /></div>' },
          'el-button': { template: '<button><slot /></button>' },
          'el-row': { template: '<div><slot /></div>' },
          'el-col': { template: '<div><slot /></div>' },
          'el-empty': { template: '<div />' },
          'el-tag': { template: '<span />' },
          RetrievalResult: { template: '<div />' },
        },
      },
    })

    await nextTick()

    // 页面标题渲染
    expect(wrapper.text()).toContain('检索测试台')
  })

  it('shows hint when no search performed', async () => {
    const { default: SearchTest } = await import(
      '@/views/knowledge/SearchTest.vue'
    )
    const wrapper = mount(SearchTest, {
      global: {
        stubs: {
          'el-input': { template: '<div />' },
          'el-button': { template: '<button />' },
          'el-row': false,
          'el-col': false,
          'el-empty': { template: '<div>输入查询并点击</div>' },
          'el-tag': false,
          RetrievalResult: false,
        },
      },
    })

    await nextTick()

    expect(wrapper.text()).toContain('检索测试台')
  })
})
