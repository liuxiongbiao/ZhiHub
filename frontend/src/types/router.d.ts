/**
 * src/types/router.d.ts
 *
 * 增强 vue-router RouteMeta 类型，使 to.meta.public / to.meta.requiresRole
 * 在 strict TypeScript 下可用（否则是 unknown，守卫里 includes() 会类型报错）。
 */
import 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    /** 公开路由（免鉴权），缺省视为受保护 */
    public?: boolean
    /** 角色白名单，缺省仅要求已登录 */
    requiresRole?: string[]
  }
}
