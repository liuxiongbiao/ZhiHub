/**
 * src/store/modules/tenant.ts
 *
 * 当前租户状态: 信息来自 GET /tenant/home。
 * AdminLayout.onMounted 和 Home.vue.onMounted 均调 fetchHome() 拉取数据。
 *
 * ── 数据流 ──
 * 页面进入 → fetchHome() → getTenantHome() → $patch(data) 更新全部字段
 *   - UI 通过 tenantStore.tenant_name / plan_type / status / current_user 响应式渲染
 *   - 401 由 request 拦截器兜底（清 token → 跳 /login），本 store 不需特殊处理
 */
import { defineStore } from 'pinia'
import { getTenantHome } from '@/api/tenant'
import type { CurrentUser } from '@/types/auth'

export const useTenantStore = defineStore('tenant', {
  state: () => ({
    tenant_id: 0,
    tenant_name: '',
    plan_type: '',
    status: '',
    current_user: null as CurrentUser | null,
    degradation_alert: false as boolean,
    warning_banner: null as string | null,
  }),

  actions: {
    async fetchHome(): Promise<void> {
      const res = await getTenantHome()
      this.$patch(res.data)
    },
  },
})
