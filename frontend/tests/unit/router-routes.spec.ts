/**
 * tests/unit/router-routes.spec.ts
 *
 * : 路由表回归——/agent/* 路由的角色守卫与 AgentLayout 内聚；
 * 保证新增坐席侧路由的同时，原有管理员路由全部保留。
 * 只读 router 实例的路由记录，不渲染组件（懒加载不触发）。
 */
import { describe, it, expect } from 'vitest'
import router from '@/router'

function routeByName(name: string) {
  return router.getRoutes().find((r) => r.name === name)
}

function routeByPath(path: string) {
  // getRoutes() 展平后子路由 path 为绝对路径
  return router.getRoutes().find((r) => r.path === path)
}

describe('router route table ()', () => {
  it('/agent/console requires both admin and agent', () => {
    const r = routeByName('agent-console')
    expect(r).toBeTruthy()
    expect(r!.meta.requiresRole).toEqual(['admin', 'agent'])
  })

  const AGENT_PAGES: Array<[string, string]> = [
    ['agent-tickets', '/agent/tickets'],
    ['agent-orders', '/agent/orders'],
    ['agent-refunds', '/agent/refunds'],
  ]

  for (const [name, path] of AGENT_PAGES) {
    it(`${path} exists with requiresRole [admin,agent]`, () => {
      const r = routeByName(name)
      expect(r).toBeTruthy()
      expect(r!.path).toBe(path)
      expect(r!.meta.requiresRole).toEqual(['admin', 'agent'])
    })
  }

  it('agent routes stay under AgentLayout (parent path /agent, not AdminLayout /)', () => {
    for (const [, path] of AGENT_PAGES) {
      const r = routeByPath(path)
      expect(r).toBeTruthy()
      // 展平记录的父级链不含 AdminLayout 下的同名管理端路径
      expect(r!.path.startsWith('/agent/')).toBe(true)
    }
    // 管理端原路由仍在
    expect(routeByPath('/ticket/list')).toBeTruthy()
    expect(routeByPath('/order/list')).toBeTruthy()
    expect(routeByPath('/order/refunds')).toBeTruthy()
  })

  it('admin routes preserved (兼容性回归)', () => {
    const names = [
      'dashboard', 'login', 'register', 'activate-email', 'accept-invite',
      'setup-wizard', 'plan-settings', 'member-invite', 'widget-config', 'audit-logs',
      'profile', 'knowledge-list', 'knowledge-search-test', 'ticket-list', 'order-list',
      'order-refunds', 'billing-usage', 'billing-invoices', 'session-detail',
      'session-monitor', 'agent-report', 'widget-preview', 'agent-console',
      'operations-dashboard',
    ]
    for (const n of names) {
      expect(routeByName(n), `route name "${n}" should exist`).toBeTruthy()
    }
  })

  it('route names are unique', () => {
    // 父布局路由（'/' 与 '/agent'）无 name，只检查具名记录
    const names = router.getRoutes().filter((r) => r.name).map((r) => String(r.name))
    expect(new Set(names).size).toBe(names.length)
  })
})
