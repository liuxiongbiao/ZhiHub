/**
 * src/permission.ts
 *
 * 路由守卫：resolveNavigation(纯函数) + registerGuard(注册) + wireUnauthorized(401 接线)。
 *
 * ── 整体架构 ──
 * 本文件拆成三个独立部分，便于 TDD 单测：
 *   1. resolveNavigation(to, auth)  → 纯函数，无外部依赖，输入路由+登录态，输出放行/重定向
 *   2. registerGuard(router)        → 注册 beforeEach 守卫，调 resolveNavigation 做决策
 *   3. wireUnauthorized(router)     → 接线 request.ts 的 setUnauthorizedHandler 回调，
 *                                     当 401 发生时清 store + 跳 /login
 *
 * ── 守卫决策树（resolveNavigation）──
 * 公开路由（meta.public=true，/login /register）
 *   ├─ 已登录 → 重定向到 /（已登录不需要看登录页）
 *   └─ 未登录 → 放行
 *
 * 受保护路由（meta.public 未定义或 false）
 *   ├─ 未登录 → 重定向 /login?redirect=原路径
 *   ├─ 已登录 + 无 requiresRole → 放行
 *   ├─ 已登录 + requiresRole 命中 → 放行
 *   └─ 已登录 + requiresRole 未命中 → 重定向 /?forbidden=1（agent 试图访问 admin-only 页）
 *
 * ── 401 回调链路 ──
 * request.ts 响应拦截器 401 → localStorage.removeItem("zhihub:user") → _unauthorizedHandler()
 *   → wireUnauthorized 注册的回调 → userStore.reset() + router.push('/login')
 *
 * ── 循环依赖防护 ──
 * permission.ts → 惰性 import('@/store/modules/user') 而非顶层静态 import
 *   （因为 store 不 import permission，形成单向依赖）
 */
import type { RouteLocationNormalized, RouteLocationRaw, Router } from 'vue-router'
import { setUnauthorizedHandler } from '@/api/request'

interface AuthSnapshot {
  isLoggedIn: boolean
  role: string | null
}

/**
 *  角色默认首页：admin → /（管理员看板），agent → /agent/console（客服工作台）。
 * 未知/空角色保守回 /。登录成功跳转（Login.vue safeRedirect）与守卫拦截共用此函数，
 * 保证"登录落地页"与"被拦截后回退页"一致。
 */
export function homePathForRole(role: string | null | undefined): string {
  return role === 'agent' ? '/agent/console' : '/'
}

/**
 * 纯决策函数——无 pinia/router 依赖，可独立单测。
 * 返回 true 放行，返回 RouteLocationRaw 重定向。
 */
export function resolveNavigation(
  to: RouteLocationNormalized,
  auth: AuthSnapshot,
): RouteLocationRaw | true {
  // A. 公开路由（meta.public）
  if (to.meta.public) {
    // A1 已登录访问登录/注册 → 送回角色首页
    if (auth.isLoggedIn && (to.name === 'login' || to.name === 'register')) {
      return { path: homePathForRole(auth.role) }
    }
    return true
  }

  // B. 受保护路由
  // B1 未登录 → 跳登录并记住来源
  if (!auth.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // B2 requiresRole 未定义 → 放行（仅需登录）
  const required = to.meta.requiresRole as string[] | undefined
  if (!required || required.length === 0) return true

  // B3 role 命中白名单 → 放行
  if (auth.role && required.includes(auth.role)) return true

  // B4 未命中 → 按角色送回对应首页（后端仍是最终防线）
  return { path: homePathForRole(auth.role), query: { forbidden: '1' } }
}

/** 注册 beforeEach 守卫，惰性取 store 避免 import 循环。 */
export function registerGuard(router: Router): void {
  router.beforeEach(async (to) => {
    const { useUserStore } = await import('@/store/modules/user')
    const userStore = useUserStore()
    const auth: AuthSnapshot = {
      isLoggedIn: userStore.isLoggedIn,
      role: userStore.role,
    }
    const decision = resolveNavigation(to, auth)
    return decision === true ? true : decision
  })
}

/** 401 回调：清 store + 跳 /login（已在 /login 时不重复） */
export function wireUnauthorized(router: Router): void {
  setUnauthorizedHandler(async () => {
    const { useUserStore } = await import('@/store/modules/user')
    const userStore = useUserStore()
    userStore.reset()
    const current = router.currentRoute.value
    if (current.name !== 'login') {
      router.push({ path: '/login', query: { redirect: current.fullPath } }).catch(() => {})
    }
  })
}
