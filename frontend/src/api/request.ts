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
import { API_BASE_URL } from '@/constants'
import {
  getAuthToken,
  getRefreshToken,
  handleAuthFailure,
  setAuthFailureHandler,
  updateAuthTokens,
} from './auth-session'

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

export function setUnauthorizedHandler(fn: () => void): void {
  setAuthFailureHandler(fn)
}

const request = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
})

interface RetryableRequestConfig {
  url?: string
  headers?: Record<string, string>
  _refreshRetried?: boolean
}

let refreshFlight: Promise<string> | null = null

function refreshAccessToken(): Promise<string> {
  if (refreshFlight) return refreshFlight
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    handleAuthFailure()
    return Promise.reject(new ApiError(401, '登录已过期'))
  }
  refreshFlight = axios.post<{
    access_token: string
    refresh_token: string
  }>(`${API_BASE_URL}/auth/refresh`, { refresh_token: refreshToken })
    .then((response) => {
      updateAuthTokens(response.data.access_token, response.data.refresh_token)
      return response.data.access_token
    })
    .catch((error) => {
      handleAuthFailure()
      throw error
    })
    .finally(() => {
      refreshFlight = null
    })
  return refreshFlight
}

// 请求拦截器: 从 localStorage 取 token 注入 Authorization
request.interceptors.request.use((config) => {
  const token = getAuthToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
    // CSRF 防护: 状态变更请求携带 X-CSRF-Token 头（复用 JWT 令牌）
    config.headers['X-CSRF-Token'] = token
  }
  return config
})

// 响应拦截器: 成功返回完整 response（调用方用 res.data 取 body）；错误归一化 ApiError
request.interceptors.response.use(
  (res) => res,
  async (error) => {
    const status = error.response?.status ?? 0
    const data = error.response?.data
    const original = error.config as RetryableRequestConfig | undefined

    if (
      status === 401
      && original
      && !original._refreshRetried
      && !original.url?.includes('/auth/refresh')
    ) {
      original._refreshRetried = true
      try {
        const accessToken = await refreshAccessToken()
        original.headers = original.headers ?? {}
        original.headers.Authorization = `Bearer ${accessToken}`
        original.headers['X-CSRF-Token'] = accessToken
        return request(original)
      } catch {
        // refreshAccessToken 已统一清理登录态并通知路由层。
      }
    } else if (status === 401) {
      handleAuthFailure()
    }

    const message = extractDetail(data)
    return Promise.reject(new ApiError(status, message, data))
  },
)

export default request
