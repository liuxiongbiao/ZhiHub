/**
 * frontend/src/router/index.ts
 *
 * 路由定义: /login、/register 公开; AdminLayout 包裹后台路由。
 * 视图/布局全懒加载，打断 router→layout→store→api 循环依赖链。
 *
 * ── 路由表设计 ──
 *   /login     → Login.vue     meta.public=true  免鉴权
 *   /register  → Register.vue  meta.public=true  免鉴权
 *   /          → AdminLayout.vue（父路由，受保护）
 *     └─ ""   → Home.vue       dashboard 首页（受保护）
 *
 * ── 懒加载策略 ──
 *   所有页面/布局用 () => import(...) 动态导入：
 *   1. 减少首屏体积（未访问的页面不加载 JS）
 *   2. 打断循环依赖：router 不静态 import layout/view → layout 不静态 import store
 *      → store 不静态 import router（单向依赖链）
 *
 * ── 守卫注册 ──
 *   路由守卫在 main.ts 中通过 registerGuard(router) 和 wireUnauthorized(router) 激活。
 *   不在本文件直接注册，便于测试时新建独立 router 实例。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/login/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/login/Register.vue'),
    meta: { public: true },
  },
  {
    path: '/activate-email',
    name: 'activate-email',
    component: () => import('@/views/login/ActivateEmail.vue'),
    meta: { public: true },
  },
  {
    path: '/accept-invite',
    name: 'accept-invite',
    component: () => import('@/views/onboarding/AcceptInvite.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/AdminLayout.vue'),
    children: [
      {
        path: '',
        name: 'dashboard',
        component: () => import('@/views/dashboard/Home.vue'),
      },
      {
        path: 'demo-center',
        name: 'demo-center',
        component: () => import('@/views/demo/DemoCenter.vue'),
        meta: { requiresRole: ['admin'] },
      },
      // 配置向导(仅 admin，后端 require_role 兜底)
      {
        path: 'setup',
        name: 'setup-wizard',
        component: () => import('@/views/onboarding/SetupWizard.vue'),
        meta: { requiresRole: ['admin'] },
      },
      {
        path: 'settings',
        name: 'plan-settings',
        component: () => import('@/views/onboarding/PlanSettings.vue'),
        meta: { requiresRole: ['admin'] },
      },
      // 规格 013 US5: 成员邀请（admin）
      {
        path: 'onboarding/members',
        name: 'member-invite',
        component: () => import('@/views/onboarding/MemberInvite.vue'),
        meta: { requiresRole: ['admin'] },
      },
      // 规格 013: Widget 品牌定制（White-label，admin）
      {
        path: 'widget/config',
        name: 'widget-config',
        component: () => import('@/views/onboarding/WidgetConfig.vue'),
        meta: { requiresRole: ['admin'] },
      },
      // 规格 013: 审计日志（admin）
      {
        path: 'audit/logs',
        name: 'audit-logs',
        component: () => import('@/views/audit/AuditLog.vue'),
        meta: { requiresRole: ['admin'] },
      },
      // 规格 013: 个人中心（密码重置 / 邮箱 / 微信绑定）
      {
        path: 'profile',
        name: 'profile',
        component: () => import('@/views/profile/Profile.vue'),
      },
      {
        path: 'knowledge/list',
        name: 'knowledge-list',
        component: () => import('@/views/knowledge/KnowledgeList.vue'),
        meta: { requiresRole: ['admin'] },
      },
      {
        path: 'knowledge/search-test',
        name: 'knowledge-search-test',
        component: () => import('@/views/knowledge/SearchTest.vue'),
        meta: { requiresRole: ['admin'] },
      },
      {
        path: 'knowledge/quality',
        name: 'knowledge-quality',
        component: () => import('@/views/knowledge/KnowledgeQuality.vue'),
        meta: { requiresRole: ['admin'] },
      },
      {
        path: 'ticket/list',
        name: 'ticket-list',
        component: () => import('@/views/ticket/TicketList.vue'),
      },
      {
        path: 'order/list',
        name: 'order-list',
        component: () => import('@/views/order/OrderList.vue'),
      },
      {
        path: 'order/refunds',
        name: 'order-refunds',
        component: () => import('@/views/order/RefundApproval.vue'),
        meta: { requiresRole: ['admin', 'agent'] },
      },
      {
        path: 'billing/usage',
        name: 'billing-usage',
        component: () => import('@/views/billing/UsageOverview.vue'),
        meta: { requiresRole: ['admin'] },  // 后端 billing 端点 require_role("admin")
      },
      {
        path: 'billing/invoices',
        name: 'billing-invoices',
        component: () => import('@/views/billing/InvoiceList.vue'),
        meta: { requiresRole: ['admin'] },
      },
      {
        path: 'analytics/overview',
        name: 'operations-dashboard',
        component: () => import('@/views/analytics/OperationsDashboard.vue'),
        meta: { requiresRole: ['admin'] },
      },
      {
        path: 'session/:id',
        name: 'session-detail',
        component: () => import('@/views/session/SessionDetail.vue'),
      },
      // 规格 013 审查 ：会话监控列表（PRD §3.8，admin）
      {
        path: 'session/monitor',
        name: 'session-monitor',
        component: () => import('@/views/session/SessionMonitor.vue'),
        meta: { requiresRole: ['admin'] },
      },
      {
        path: 'agent-report',
        name: 'agent-report',
        component: () => import('@/views/agent-report/DeepAgentReport.vue'),
      },
      {
        path: 'platform/operations',
        name: 'platform-operations',
        component: () => import('@/views/platform/PlatformOperations.vue'),
        meta: { requiresRole: ['admin'] },
      },
      // Widget 预览(终端用户聊天 demo)
      {
        path: 'widget/preview',
        name: 'widget-preview',
        component: () => import('@/views/widget-demo/WidgetPreview.vue'),
      },
    ],
  },
  {
    path: '/agent',
    component: () => import('@/layouts/AgentLayout.vue'),
    children: [
      {
        path: 'console',
        name: 'agent-console',
        component: () => import('@/views/agent/AgentConsole.vue'),
        meta: { requiresRole: ['admin', 'agent'] },
      },
      // : 坐席侧内聚路由——复用管理端同款视图，但挂在 AgentLayout 下，
      // 客服不必跳出工作台布局（原先 AgentLayout 菜单指向 /ticket/list 等管理端路径）
      {
        path: 'tickets',
        name: 'agent-tickets',
        component: () => import('@/views/ticket/TicketList.vue'),
        meta: { requiresRole: ['admin', 'agent'] },
      },
      {
        path: 'orders',
        name: 'agent-orders',
        component: () => import('@/views/order/OrderList.vue'),
        meta: { requiresRole: ['admin', 'agent'] },
      },
      {
        path: 'refunds',
        name: 'agent-refunds',
        component: () => import('@/views/order/RefundApproval.vue'),
        meta: { requiresRole: ['admin', 'agent'] },
      },
      //  收尾三: 坐席个人中心——复用管理端 Profile.vue，但挂在 AgentLayout 下，
      // 客服不必跳回 AdminLayout 的 /profile
      {
        path: 'profile',
        name: 'agent-profile',
        component: () => import('@/views/profile/Profile.vue'),
        meta: { requiresRole: ['admin', 'agent'] },
      },
    ],
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
