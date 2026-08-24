/**
 * src/types/auth.ts
 *
 * 认证 / 租户相关 DTO，逐字段对齐后端 schemas/auth.py 与 schemas/tenant.py。
 */

export interface RegisterRequest {
  phone: string
  password: string
  tenant_name: string
  code: string
}

// 规格 013 US1: 多方式登录/注册扩展（PRD §3.1.1/3.1.2）
export interface SendCodeResponse {
  message: string
  code: string
  expires_in: number
}

export interface SmsLoginRequest {
  phone: string
  code: string
}

export interface EmailRegisterRequest {
  email: string
  password: string
  tenant_name: string
  plan_type?: string
}

export interface EmailRegisterResponse {
  user_id: number
  tenant_id: number
  email: string
  role: string
  plan_type: string
  email_verified?: boolean
  activation_link?: string | null
}

export interface EmailLoginRequest {
  email: string
  password: string
}

export interface WechatAuthResponse {
  auth_url: string
}

export interface WechatCallbackResponse {
  need_bind: boolean
  openid: string | null
  access_token: string | null
  refresh_token: string | null
  token_type: string
  user_id: number | null
  tenant_id: number | null
  role: string | null
}

export interface RegisterResponse {
  user_id: number
  tenant_id: number
  phone: string
  role: string
  plan_type: string
}

export interface LoginRequest {
  phone: string
  password: string
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user_id: number
  tenant_id: number
  role: string
  phone: string | null
}

export interface MessageResponse {
  message: string
}

export type DemoPersona = 'admin' | 'agent1' | 'agent2'

export interface LoginOptionsResponse {
  email_login: boolean
  sms_login: boolean
  wechat_login: boolean
  payment: boolean
  demo_quick_login: boolean
  demo_personas: DemoPersona[]
}

export interface RefreshResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface WsTokenResponse {
  token: string
  expires_in: number
}

export interface CurrentUser {
  user_id: number
  role: string
}

export interface TenantHomeResponse {
  tenant_id: number
  tenant_name: string
  plan_type: string
  status: string
  current_user: CurrentUser
  degradation_alert?: boolean
  warning_banner?: string | null
}
