/**
 * tests/setup.ts
 *
 * vitest jsdom 环境补齐，保证 Element Plus 组件在测试中不因缺失浏览器 API 而报错。
 * 每次测试前清空 localStorage，避免用例间 token 状态串扰。
 */
import { beforeEach } from 'vitest'

// Element Plus 的 el-input / el-popper 等依赖 ResizeObserver，jsdom 没有
class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
;(globalThis as any).ResizeObserver = FakeResizeObserver

// Element Plus 部分组件读取 matchMedia 判断暗色模式等
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})

beforeEach(() => {
  localStorage.clear()
})
