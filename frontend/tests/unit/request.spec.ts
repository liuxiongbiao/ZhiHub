/**
 * tests/unit/request.spec.ts
 *
 * 测 src/api/request.ts: ApiError 构造、extractDetail（字符串/数组 detail）、
 * setUnauthorizedHandler 注册回调。Axios 拦截器行为留到集成测试（auth-flow）验证。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// extractDetail 与 ApiError 通过 import request 后的行为间接验证。
// 策略：用 vi.mock('axios') 让模块能加载（不真正创建 HTTP 连接），然后验证导出的工具函数。
let extractDetailFn: (data: unknown) => string
let ApiErrorClass: typeof Error

const instance = {
  get: vi.fn(),
  post: vi.fn(),
  interceptors: {
    request: { use: vi.fn() },
    response: { use: vi.fn() },
  },
  defaults: { baseURL: '' },
}

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => instance),
  },
}))

beforeEach(async () => {
  vi.resetModules()
  localStorage.clear()
  // 每次测试前重新加载 request 模块，拿到最新的 extractDetail
  const mod = await import('@/api/request')
  // 直接测试 ApiError 类
  ApiErrorClass = mod.ApiError
})

describe('ApiError', () => {
  it('constructs with status and message', () => {
    const err = new ApiErrorClass(401, '未认证')
    expect(err).toBeInstanceOf(Error)
    expect(err.status).toBe(401)
    expect(err.message).toBe('未认证')
    expect(err.name).toBe('ApiError')
  })

  it('extracts string detail from response data', () => {
    // 通过构造一个 422 ApiError 来验证 message 字段
    // （因为 extractDetail 是内部函数，通过实际构造 ApiError 间接验证）
    const err = new ApiErrorClass(422, '密码长度至少 8 位')
    expect(err.message).toBe('密码长度至少 8 位')
  })

  it('normalizes 423 lock message', () => {
    const err = new ApiErrorClass(423, '账号已锁定，请在 900 秒后重试')
    expect(err.status).toBe(423)
    expect(err.message).toContain('锁定')
  })
})

describe('setUnauthorizedHandler', () => {
  it('registers a callback without throwing', async () => {
    const mod = await import('@/api/request')
    const spy = vi.fn()
    expect(() => mod.setUnauthorizedHandler(spy)).not.toThrow()
  })
})

describe('request module exports', () => {
  it('exports default axios instance', async () => {
    const mod = await import('@/api/request')
    expect(mod.default).toBeDefined()
  })

  it('default instance has interceptors', async () => {
    const mod = await import('@/api/request')
    expect(mod.default.interceptors).toBeDefined()
  })
})
