/**
 * src/api/tenant.ts
 *
 * 租户接口封装。AdminLayout / Home 页面通过 tenantStore.fetchHome() 调用本模块。
 *
 * ── 调用链 ──
 * AdminLayout.onMounted → tenantStore.fetchHome() → api.tenant.getTenantHome()
 *   → request.get('/tenant/home') → 请求拦截器注入 token → HTTP → 后端 /api/v1/tenant/home
 *   → TenantHomeResponse → tenantStore.$patch 更新状态 → UI 响应式渲染
 *
 * ── 注意 ──
 * request 响应拦截器返回完整 axios response（非 body），故此处显式取 res.data 解包后返回，
 * 让 store 的 $patch 直接拿到 body（规格 012 修复：原实现漏取 .data，导致租户名永远为空）。
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


// ———— 规格 013 US5: 成员邀请 / 审计日志 / Widget 品牌定制 ————

/** 邀请响应 */
export interface InviteResponse {
  invite_url: string
  invite_token: string
  expires_in: number
}

export interface AcceptInviteRequest {
  token: string
  phone?: string
  password?: string
}

export interface AcceptInviteResponse {
  user_id: number
  tenant_id: number
  role: 'agent'
  access_token: string
  refresh_token: string
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

/** : Widget 完整配置查询响应（品牌字段 + 域名白名单；不含 API Key 任何形态） */
export interface WidgetConfigQuery {
  tenant_id: number
  theme_color: string
  logo_url: string | null
  welcome_text: string | null
  domains: string[]
}

/** : 域名白名单响应 */
export interface WidgetDomains { domains: string[] }

/** admin 邀请坐席成员（受 seat_limit 约束，超限 409） */
export async function inviteMember(phone: string) {
  const res = await request.post('/tenant/invite', { phone })
  return res
}

/** 接受邀请；已登录用户仅传 token，匿名兼容契约可附手机号和密码。 */
export function acceptInvite(body: AcceptInviteRequest) {
  return request.post<AcceptInviteResponse>('/tenant/invite/accept', body)
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

/** : 查询 Widget 完整配置（品牌 + 域名白名单；仅 admin） */
export async function getWidgetConfig() {
  const res = await request.get('/tenant/widget')
  return res
}

/** : 生成/轮换 Widget API Key（仅生成时返回一次，须立即复制保存） */
export async function rotateWidgetApiKey() {
  const res = await request.post('/tenant/widget/api-key')
  return res
}

/** : 查询 Widget 域名白名单（仅 admin） */
export async function getWidgetDomains() {
  const res = await request.get('/tenant/widget/domains')
  return res
}

/** : 修改 Widget 域名白名单（仅 admin） */
export async function setWidgetDomains(domains: string[]) {
  const res = await request.put('/tenant/widget/domains', { domains })
  return res
}

/** 获取 Widget 嵌入代码 */
export async function getWidgetEmbedCode() {
  const res = await request.get('/tenant/widget/embed-code')
  return res
}

/** 查询退款审批阈值（PRD §3.6，仅 admin） */
export async function getRefundThreshold() {
  const res = await request.get('/tenant/refund-threshold')
  return res
}

/** 更新退款审批阈值（PRD §3.6，仅 admin） */
export async function updateRefundThreshold(refund_approval_threshold: number) {
  const res = await request.put('/tenant/refund-threshold', { refund_approval_threshold })
  return res
}

/** 租户核心数据导出（PRD §8.5 最小实现，CSV 打包 ZIP，仅 admin） */
export function exportTenantData() {
  return request.get('/tenant/export', { responseType: 'blob' })
}
