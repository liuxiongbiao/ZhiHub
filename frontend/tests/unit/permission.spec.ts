/**
 * tests/unit/permission.spec.ts
 *
 * 测 src/permission.ts 的纯函数 resolveNavigation —— 不 mount、不碰 pinia，
 * 直接调函数验证守卫分支（对应 plan §4 的完整分支表）。
 */
import { describe, it, expect } from 'vitest'
import { resolveNavigation } from '@/permission'
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

describe('resolveNavigation', () => {
  // ── 公开路由 ──
  it('public route, not logged in → pass through', () => {
    const r = resolveNavigation(
      to({ name: 'login', meta: { public: true } }),
      { isLoggedIn: false, role: null },
    )
    expect(r).toBe(true)
  })

  it('public route, logged in, visiting login → redirect to /', () => {
    const r = resolveNavigation(
      to({ name: 'login', meta: { public: true } }),
      { isLoggedIn: true, role: 'admin' },
    )
    expect(r).toEqual({ path: '/' })
  })

  it('public route, logged in, visiting register → redirect to /', () => {
    const r = resolveNavigation(
      to({ name: 'register', meta: { public: true } }),
      { isLoggedIn: true, role: 'admin' },
    )
    expect(r).toEqual({ path: '/' })
  })

  // ── 受保护路由 ──
  it('protected route, not logged in → redirect to /login with redirect query', () => {
    const r = resolveNavigation(
      to({ fullPath: '/dashboard' }),
      { isLoggedIn: false, role: null },
    )
    expect(r).toEqual({ path: '/login', query: { redirect: '/dashboard' } })
  })

  it('protected route, logged in, no requiresRole → pass through', () => {
    const r = resolveNavigation(
      to({}),
      { isLoggedIn: true, role: 'agent' },
    )
    expect(r).toBe(true)
  })

  it('protected route, requiresRole admin, agent → redirect to /', () => {
    const r = resolveNavigation(
      to({ meta: { requiresRole: ['admin'] } }),
      { isLoggedIn: true, role: 'agent' },
    )
    expect(r).toEqual({ path: '/', query: { forbidden: '1' } })
  })

  it('protected route, requiresRole admin, admin → pass through', () => {
    const r = resolveNavigation(
      to({ meta: { requiresRole: ['admin'] } }),
      { isLoggedIn: true, role: 'admin' },
    )
    expect(r).toBe(true)
  })
})
