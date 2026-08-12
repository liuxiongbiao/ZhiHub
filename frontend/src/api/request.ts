/**
 * src/api/request.ts
 *
 * Axios 实例封装：baseURL、请求拦截器注入 Bearer(从 localStorage 读，不 import store 以免循环依赖)、
 * 响应拦截器返回完整 response + 失败归一化 ApiError + 401 清存储并触发回调。
 *
 * ── 数据流 ──
 * 前端任意 api 函数调用 request.get/post →
 *   请求拦截器: 从 localStorage("zhihub:user") 读 token → 注入 Authorization Bearer
 *   HTTP 请求 → Vite proxy 转发 → 后端 FastAPI
 *   响应拦截器:
 *     ├─ 成功 → return res（完整 AxiosResponse，调用方取 res.data）
 *     └─ 失败 → extractDetail 归一化 → reject ApiError{status, message, data}
 *        └─ 401 → 清 localStorage + 调 unauthorizedHandler（由 permission.ts 注入）
 *
 * ── 循环依赖防护 ──
 *   request.ts → 只读 localStorage，不 import router/store
 *   store → import api 模块（正常依赖方向）
 *   router → 懒加载 views（不静态 import layout/store）
 *   这三条防线保证请求层/状态层/路由层不会形成 import 环
 */
import axios from 'axios'
import { AUTH_STORAGE_KEY, API_BASE_URL } from '@/constants'

export class ApiError extends Error {
  constructor(
    public status: number,
    public message: string,
    public data?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function extractDetail(data: unknown): string {
  if (!data || typeof data !== 'object') return '请求失败'
  const obj = data as Record<string, unknown>
  const detail = obj.detail
  if (typeof detail === 'string') return detail
  // pydantic 422 校验错误: detail 是数组，取第一个 msg
  if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === 'string') {
    return detail[0].msg
  }
  return '请求失败'
}

let _unauthorizedHandler: (() => void) | null = null

export function setUnauthorizedHandler(fn: () => void): void {
  _unauthorizedHandler = fn
}

const request = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
})

// 请求拦截器: 从 localStorage 取 token 注入 Authorization
request.interceptors.request.use((config) => {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (raw) {
      const stored = JSON.parse(raw) as { token?: string }
      if (stored.token) {
        config.headers.Authorization = `Bearer ${stored.token}`
        // CSRF 防护: 状态变更请求携带 X-CSRF-Token 头（复用 JWT 令牌）
        config.headers['X-CSRF-Token'] = stored.token
      }
    }
  } catch {
    // localStorage 数据损坏，忽略
  }
  return config
})

// 响应拦截器: 成功返回完整 response（调用方用 res.data 取 body）；错误归一化 ApiError
request.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status ?? 0
    const data = error.response?.data

    if (status === 401) {
      localStorage.removeItem(AUTH_STORAGE_KEY)
      _unauthorizedHandler?.()
    }

    const message = extractDetail(data)
    return Promise.reject(new ApiError(status, message, data))
  },
)

export default request
