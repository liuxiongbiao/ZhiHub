/**
 * src/api/tenant.ts
 *
 * 租户接口封装。AdminLayout / Home 页面通过 tenantStore.fetchHome() 调用本模块。
 *
 * ── 调用链 ──
 * AdminLayout.onMounted -> tenantStore.fetchHome() -> api.tenant.getTenantHome()
 *   -> request.get('/tenant/home') -> 请求拦截器注入 token -> HTTP -> 后端 /api/v1/tenant/home
 *   -> TenantHomeResponse -> tenantStore.$patch 更新状态 -> UI 响应式渲染
 *
 * ── 注意 ──
 * request 响应拦截器返回完整 axios response（非 body），故此处显式取 res.data 解包后返回，
 * 让 store 的 $patch 直接拿到 body（012 修复：原实现漏取 .data，导致租户名永远为空）。
 */
import request from './request'

/** 配置向导状态（对齐后端 WizardStatusResponse） */
export interface WizardStatus {
  /** 当前步骤：basic / knowledge / mcp / team / done */
  current_step: string
  /** 是否已完成（current_step == done） */
  completed: boolean
  /** 总步数 */
  total_steps: number
  /** 步骤全表 */
  steps: string[]
}

export async function getTenantHome() {
  const res = await request.get('/tenant/home')
  return res
}

/** 查询配置向导状态（GET /tenant/wizard，仅 admin） */
export async function getWizardStatus() {
  const res = await request.get('/tenant/wizard')
  return res
}

/** 推进配置向导到下一步（POST /tenant/wizard/next，仅 admin；done 再推进后端返 409） */
export async function advanceWizard() {
  const res = await request.post('/tenant/wizard/next')
  return res
}


// ---- 013 US5: 成员邀请 / 审计日志 / Widget 品牌定制 ----

/** 邀请响应 */
export interface InviteResponse {
  invite_url?: string
  token?: string
}

/** 审计日志项 */
export interface AuditLogItem {
  id: number
  tenant_id: number
  user_id: number | null
  action: string
  target_type: string | null
  target_id: string | number | null
  detail: string | null
  created_at: string
}

/** Widget 配置请求 */
export interface WidgetConfigBody {
  theme_color?: string
  logo_url?: string
  welcome_text?: string
}

/** admin 邀请坐席成员（受 seat_limit 约束，超限 409） */
export async function inviteMember(phone: string) {
  const res = await request.post('/tenant/invite', { phone })
  return res
}

/** 审计日志列表（仅 admin） */
export async function getAuditLogs(page = 1, page_size = 20) {
  const res = await request.get('/tenant/audit-logs', { params: { page, page_size } })
  return res
}

/** 更新 Widget 品牌配置 */
export async function updateWidgetConfig(body: WidgetConfigBody) {
  const res = await request.post('/tenant/widget', body)
  return res
}

/** 获取 Widget 嵌入代码 */
export async function getWidgetEmbedCode() {
  const res = await request.get('/tenant/widget/embed-code')
  return res
}
