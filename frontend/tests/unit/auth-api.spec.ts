/**
 * tests/unit/auth-api.spec.ts
 * 013 US1 (T016): auth api 调用契约与路径断言。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { mockRequest } = vi.hoisted(() => ({
  mockRequest: { get: vi.fn(), post: vi.fn() }
}))
vi.mock('@/api/request', () => ({ default: mockRequest }))

import { register, login, logout, smsSend, loginSms, registerEmail, loginEmail, wechatAuth, wechatCallback } from '@/api/auth'

beforeEach(() => { vi.clearAllMocks() })

describe('auth api (013 US1)', () => {
  it('register posts to /auth/register with code', async () => {
    mockRequest.post.mockResolvedValue({ data: { user_id: 1 } })
    await register({ phone: '138', password: 'abc12345', tenant_name: 'T', code: '123456' })
    expect(mockRequest.post).toHaveBeenCalledWith('/auth/register', { phone: '138', password: 'abc12345', tenant_name: 'T', code: '123456' })
  })

  it('smsSend posts to /auth/sms/send', async () => {
    mockRequest.post.mockResolvedValue({ data: { code: '123456' } })
    await smsSend('13800138000')
    expect(mockRequest.post).toHaveBeenCalledWith('/auth/sms/send', { phone: '13800138000' })
  })

  it('loginSms posts to /auth/login/sms', async () => {
    mockRequest.post.mockResolvedValue({ data: { access_token: 't' } })
    await loginSms({ phone: '138', code: '123456' })
    expect(mockRequest.post).toHaveBeenCalledWith('/auth/login/sms', { phone: '138', code: '123456' })
  })

  it('registerEmail posts to /auth/register/email', async () => {
    mockRequest.post.mockResolvedValue({ data: { user_id: 1 } })
    await registerEmail({ email: 'a@b.com', password: 'abc12345', tenant_name: 'T' })
    expect(mockRequest.post).toHaveBeenCalledWith('/auth/register/email', { email: 'a@b.com', password: 'abc12345', tenant_name: 'T' })
  })

  it('loginEmail posts to /auth/login/email', async () => {
    mockRequest.post.mockResolvedValue({ data: { access_token: 't' } })
    await loginEmail({ email: 'a@b.com', password: 'abc12345' })
    expect(mockRequest.post).toHaveBeenCalledWith('/auth/login/email', { email: 'a@b.com', password: 'abc12345' })
  })

  it('wechatAuth gets /auth/wechat/auth with redirect_uri', async () => {
    mockRequest.get.mockResolvedValue({ data: { auth_url: 'u' } })
    await wechatAuth('http://cb')
    expect(mockRequest.get).toHaveBeenCalledWith('/auth/wechat/auth', { params: { redirect_uri: 'http://cb' } })
  })

  it('wechatCallback gets /auth/wechat/callback with code+state', async () => {
    mockRequest.get.mockResolvedValue({ data: { need_bind: false } })
    await wechatCallback('wxcode', 'state1')
    expect(mockRequest.get).toHaveBeenCalledWith('/auth/wechat/callback', { params: { code: 'wxcode', state: 'state1' } })
  })
})
