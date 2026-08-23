/**
 * src/api/auth-session.ts
 * 职责: Axios 与原生 fetch 共用的登录令牌读取、清理及失效通知。
 */
import { AUTH_STORAGE_KEY } from '@/constants'

let unauthorizedHandler: (() => void) | null = null

export function getAuthToken(): string {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (!raw) return ''
    const stored = JSON.parse(raw) as { token?: string }
    return stored.token ?? ''
  } catch {
    return ''
  }
}

export function getRefreshToken(): string {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (!raw) return ''
    const stored = JSON.parse(raw) as { refresh_token?: string }
    return stored.refresh_token ?? ''
  } catch {
    return ''
  }
}

export function updateAuthTokens(accessToken: string, refreshToken: string): void {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    const stored = raw ? JSON.parse(raw) as Record<string, unknown> : {}
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({
      ...stored,
      token: accessToken,
      refresh_token: refreshToken,
    }))
  } catch {
    handleAuthFailure()
  }
}

export function setAuthFailureHandler(handler: () => void): void {
  unauthorizedHandler = handler
}

export function handleAuthFailure(): void {
  localStorage.removeItem(AUTH_STORAGE_KEY)
  unauthorizedHandler?.()
}
