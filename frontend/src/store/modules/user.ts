/**
 * src/store/modules/user.ts
 *
 * 用户状态: token / user_id / tenant_id / role，持久化到 localStorage。
 * token 单一来源是 localStorage（request 拦截器直接读，避免与 store 循环依赖）。
 *
 * ── 数据流 ──
 * 页面加载 → readStoredUser() 从 localStorage("zhihub:user") 水合 state
 *   - 刷新页面后 token/role 自动恢复，permission.ts 守卫可用 isLoggedIn 判断
 *
 * login()  → api.login() → 成功 → 写 state + persist() 写 localStorage
 * register() → api.register() → 返回 RegisterResponse（不改登录态，register 无 token）
 * logout() → try api.logout() → finally reset()（无条件清本地，后端不可达也要清）
 *
 * ── 关键设计 ──
 * - register 不落登录态：后端 /auth/register 只建账号不返回 token，
 *   因此 Register.vue 在 register 成功后还需调 login() 实现自动登录
 * - logout 无条件清理：try/finally 保证即使后端 logout 因 token 已过期返回 401，
 *   前端也一定清掉 localStorage，避免"已登出但本地仍有旧 token"的死锁
 */
import { defineStore } from 'pinia'
import { login as apiLogin, register as apiRegister, logout as apiLogout, loginSms as apiLoginSms, loginEmail as apiLoginEmail } from '@/api/auth'
import { AUTH_STORAGE_KEY } from '@/constants'
import type { LoginRequest, LoginResponse, RegisterRequest, RegisterResponse, SmsLoginRequest, EmailLoginRequest } from '@/types/auth'

interface UserState {
  token: string
  refresh_token: string
  user_id: number | null
  tenant_id: number | null
  role: 'admin' | 'agent' | null
  phone: string | null
}

function readStoredUser(): UserState {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      return {
        token: parsed.token ?? '',
        refresh_token: parsed.refresh_token ?? '',
        user_id: parsed.user_id ?? null,
        tenant_id: parsed.tenant_id ?? null,
        role: parsed.role ?? null,
        phone: parsed.phone ?? null,
      }
    }
  } catch { /* 存储损坏则回退空状态 */ }
  return { token: '', refresh_token: '', user_id: null, tenant_id: null, role: null, phone: null }
}

export const useUserStore = defineStore('user', {
  state: (): UserState => readStoredUser(),

  getters: {
    isLoggedIn: (s) => !!s.token,
  },

  actions: {
    async login(payload: LoginRequest): Promise<LoginResponse> {
      const res = await apiLogin(payload)
      const data = res.data
      this.token = data.access_token
      this.refresh_token = data.refresh_token
      this.user_id = data.user_id
      this.tenant_id = data.tenant_id
      this.role = data.role as 'admin' | 'agent'
      this.phone = data.phone ?? null
      this.persist()
      return data
    },

    async register(payload: RegisterRequest): Promise<RegisterResponse> {
      const res = await apiRegister(payload)
      return res.data // 仅注册，不落登录态（register 无 token）
    },


    async loginSms(payload: SmsLoginRequest): Promise<LoginResponse> {
      const res = await apiLoginSms(payload)
      const data = res.data
      this.token = data.access_token
      this.refresh_token = data.refresh_token
      this.user_id = data.user_id
      this.tenant_id = data.tenant_id
      this.role = data.role as 'admin' | 'agent'
      this.phone = data.phone ?? null
      this.persist()
      return data
    },

    async loginEmail(payload: EmailLoginRequest): Promise<LoginResponse> {
      const res = await apiLoginEmail(payload)
      const data = res.data
      this.token = data.access_token
      this.refresh_token = data.refresh_token
      this.user_id = data.user_id
      this.tenant_id = data.tenant_id
      this.role = data.role as 'admin' | 'agent'
      this.phone = data.phone ?? null
      this.persist()
      return data
    },
    async logout(): Promise<void> {
      try {
        await apiLogout()
      } catch { /* 后端不可达/已失效也要清理本地 */ }
      this.reset()
    },

    persist(): void {
      localStorage.setItem(
        AUTH_STORAGE_KEY,
        JSON.stringify({
          token: this.token,
          refresh_token: this.refresh_token,
          user_id: this.user_id,
          tenant_id: this.tenant_id,
          role: this.role,
          phone: this.phone,
        }),
      )
    },

    reset(): void {
      this.$patch({
        token: '',
        refresh_token: '',
        user_id: null,
        tenant_id: null,
        role: null,
        phone: null,
      })
      localStorage.removeItem(AUTH_STORAGE_KEY)
    },
  },
})
