/**
 * tests/unit/permission.spec.ts
 *
 * 测 src/permission.ts 的纯函数 resolveNavigation/homePathForRole —— 不 mount、不碰 pinia，
 * 直接调函数验证守卫分支（对应 plan §4 的完整分支表 +  角色默认首页）。
 */
import { describe, it, expect } from 'vitest'
import { resolveNavigation, homePathForRole } from '@/permission'
import type { RouteLocationNormalized } from 'vue-router'

function to(overrides: Partial<RouteLocationNormalized> = {}): RouteLocationNormalized {
  return {
    name: 'dashboard',
    path: '/',
    fullPath: '/',
    meta: {},
    ...overrides,
  } as RouteLocationNormalized
}

describe('homePathForRole ()', () => {
  it('agent -> /agent/console', () => {
    expect(homePathForRole('agent')).toBe('/agent/console')
  })

  it('admin -> /', () => {
    expect(homePathForRole('admin')).toBe('/')
  })

  it('null / unknown role -> / (保守回管理端首页)', () => {
    expect(homePathForRole(null)).toBe('/')
    expect(homePathForRole(undefined)).toBe('/')
    expect(homePathForRole('widget')).toBe('/')
  })
})

describe('resolveNavigation', () => {
  // ── 公开路由 ──
  it('public route, not logged in -> pass through', () => {
    const r = resolveNavigation(
      to({ name: 'login', meta: { public: true } }),
      { isLoggedIn: false, role: null },
    )
    expect(r).toBe(true)
  })

  it('public route, logged in admin, visiting login -> redirect to /', () => {
    const r = resolveNavigation(
      to({ name: 'login', meta: { public: true } }),
      { isLoggedIn: true, role: 'admin' },
    )
    expect(r).toEqual({ path: '/' })
  })

  it('public route, logged in admin, visiting register -> redirect to /', () => {
    const r = resolveNavigation(
      to({ name: 'register', meta: { public: true } }),
      { isLoggedIn: true, role: 'admin' },
    )
    expect(r).toEqual({ path: '/' })
  })

  it('public route, logged in agent, visiting login -> redirect to /agent/console', () => {
    const r = resolveNavigation(
      to({ name: 'login', meta: { public: true } }),
      { isLoggedIn: true, role: 'agent' },
    )
    expect(r).toEqual({ path: '/agent/console' })
  })

  it('public route, logged in agent, visiting register -> redirect to /agent/console', () => {
    const r = resolveNavigation(
      to({ name: 'register', meta: { public: true } }),
      { isLoggedIn: true, role: 'agent' },
    )
    expect(r).toEqual({ path: '/agent/console' })
  })

  it('public route, logged in, visiting other public pages (activate-email) -> pass through', () => {
    const r = resolveNavigation(
      to({ name: 'activate-email', meta: { public: true } }),
      { isLoggedIn: true, role: 'agent' },
    )
    expect(r).toBe(true)
  })

  // ── 受保护路由 ──
  it('protected route, not logged in -> redirect to /login with redirect query', () => {
    const r = resolveNavigation(
      to({ fullPath: '/dashboard' }),
      { isLoggedIn: false, role: null },
    )
    expect(r).toEqual({ path: '/login', query: { redirect: '/dashboard' } })
  })

  it('protected route, logged in, no requiresRole -> pass through', () => {
    const r = resolveNavigation(
      to({}),
      { isLoggedIn: true, role: 'agent' },
    )
    expect(r).toBe(true)
  })

  it('protected route, requiresRole admin, agent -> redirect to agent home with forbidden flag', () => {
    const r = resolveNavigation(
      to({ meta: { requiresRole: ['admin'] } }),
      { isLoggedIn: true, role: 'agent' },
    )
    expect(r).toEqual({ path: '/agent/console', query: { forbidden: '1' } })
  })

  it('protected route, requiresRole admin, admin -> pass through', () => {
    const r = resolveNavigation(
      to({ meta: { requiresRole: ['admin'] } }),
      { isLoggedIn: true, role: 'admin' },
    )
    expect(r).toBe(true)
  })

  it('protected route, requiresRole [admin,agent], agent -> pass through (agent-console)', () => {
    const r = resolveNavigation(
      to({ meta: { requiresRole: ['admin', 'agent'] } }),
      { isLoggedIn: true, role: 'agent' },
    )
    expect(r).toBe(true)
  })

  it('protected route, role mismatch for admin (agent-only page) -> redirect to /', () => {
    const r = resolveNavigation(
      to({ meta: { requiresRole: ['agent'] } }),
      { isLoggedIn: true, role: 'admin' },
    )
    expect(r).toEqual({ path: '/', query: { forbidden: '1' } })
  })
})
