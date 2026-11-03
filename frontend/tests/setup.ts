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

function createMemoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length() { return data.size },
    clear() { data.clear() },
    getItem(key: string) { return data.has(key) ? data.get(key)! : null },
    key(index: number) { return Array.from(data.keys())[index] ?? null },
    removeItem(key: string) { data.delete(key) },
    setItem(key: string, value: string) { data.set(String(key), String(value)) },
  }
}

const memoryStorage = createMemoryStorage()
for (const target of [globalThis, window]) {
  try {
    Object.defineProperty(target, 'localStorage', {
      configurable: true,
      writable: true,
      value: memoryStorage,
    })
  } catch {
    // Node 26 的 localStorage 可能不可重新定义；测试仍优先使用这份内存实现。
  }
}

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
  memoryStorage.clear()
})
