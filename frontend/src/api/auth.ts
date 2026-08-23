/**
 * src/api/auth.ts
 * 认证接口封装: register(含验证码) / login / logout / sms / email / wechat。
 * request 响应拦截器返回完整 AxiosResponse（非 body），调用方取 res.data 解包。
 * 规格 013 US1: 多方式登录与注册短信验证（PRD §3.1.1/3.1.2）
 */
import request from './request'
import type {
  RegisterRequest, RegisterResponse, LoginRequest, LoginResponse, MessageResponse,
  SendCodeResponse, SmsLoginRequest, EmailRegisterRequest, EmailRegisterResponse,
  EmailLoginRequest, WechatAuthResponse, WechatCallbackResponse, WsTokenResponse,
} from '@/types/auth'

export function register(body: RegisterRequest) {
  return request.post<RegisterResponse>('/auth/register', body)
}

export function login(body: LoginRequest) {
  return request.post<LoginResponse>('/auth/login', body)
}

export function logout() {
  let refreshToken = ''
  try {
    refreshToken = JSON.parse(localStorage.getItem('zhihub:user') ?? '{}').refresh_token ?? ''
  } catch { /* 损坏的本地存储交给 store reset 清理 */ }
  return request.post<MessageResponse>('/auth/logout', {
    refresh_token: refreshToken || undefined,
  })
}

/** 坐席工作台 WebSocket 短效令牌 */
export function getWsToken() {
  return request.post<WsTokenResponse>('/auth/ws-token')
}

// ———— 规格 013 US1: 多方式登录/注册扩展 ————

/** 发送短信验证码（PRD §3.1.1，demo 模式回传 code） */
export function smsSend(phone: string) {
  return request.post<SendCodeResponse>('/auth/sms/send', { phone })
}

/** 手机验证码登录 */
export function loginSms(body: SmsLoginRequest) {
  return request.post<LoginResponse>('/auth/login/sms', body)
}

/** 邮箱注册 */
export function registerEmail(body: EmailRegisterRequest) {
  return request.post<EmailRegisterResponse>('/auth/register/email', body)
}

/** 邮箱密码登录 */
export function loginEmail(body: EmailLoginRequest) {
  return request.post<LoginResponse>('/auth/login/email', body)
}

/** 公开邮箱激活端点；token 来自 /activate-email 查询参数。 */
export function activateEmail(token: string) {
  return request.post('/auth/email/activate', { token })
}

/** 微信扫码授权 URL */
export function wechatAuth(redirect_uri: string) {
  return request.get<WechatAuthResponse>('/auth/wechat/auth', { params: { redirect_uri } })
}

/** 微信扫码回调 */
export function wechatCallback(code: string, state?: string) {
  return request.get<WechatCallbackResponse>('/auth/wechat/callback', { params: { code, state } })
}

// ———— 规格 013 个人中心：密码重置 / 邮箱绑定 / 微信绑定 ————

/** 通用验证码发送（sms / email，个人中心场景） */
export function sendCode(target: string, codeType: 'sms' | 'email' = 'sms') {
  return request.post<SendCodeResponse>('/auth/code/send', { target, code_type: codeType })
}

/** 密码重置（验证码验证身份） */
export function passwordReset(body: { target: string; code: string; new_password: string; code_type?: 'sms' | 'email' }) {
  return request.post<MessageResponse>('/auth/password/reset', body)
}

/** 绑定邮箱（需登录） */
export function bindEmail(email: string, code: string) {
  return request.post<MessageResponse>('/auth/bind/email', { email, code })
}

/** 绑定手机号（需登录，PRD §3.1 账号绑定反向闭环） */
export function bindPhone(phone: string, code: string) {
  return request.post<MessageResponse>('/auth/bind/phone', { phone, code })
}

/** 微信绑定（需登录，code 为微信授权码） */
export function wechatBind(code: string) {
  return request.post<{ message: string; openid: string }>('/auth/wechat/bind', { code })
}
