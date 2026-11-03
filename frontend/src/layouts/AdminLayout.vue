<template>
  <el-container class="admin-layout">
    <Teleport to="body">
      <button v-if="navOpen" type="button" class="nav-backdrop" aria-label="关闭导航" @click="navOpen = false"></button>
    </Teleport>
    <el-aside width="244px" class="sidebar" :class="{ 'is-open': navOpen }">
      <div class="logo">
        <span class="logo-core">枢</span>
        <span class="logo-copy"><strong>智枢 ZhiHub</strong><small>客服中枢</small></span>
      </div>

      <div class="workspace-chip">
        <span class="workspace-dot"></span>
        <span><small>当前企业空间</small><strong>{{ tenantStore.tenant_name || '正在载入' }}</strong></span>
      </div>

      <el-menu router :default-active="$route.path" class="side-menu" unique-opened>
        <p class="menu-caption">总览</p>
        <el-menu-item index="/"><el-icon><Odometer /></el-icon><span>服务驾驶舱</span></el-menu-item>
        <el-menu-item v-if="userStore.role === 'admin'" index="/demo-center" class="demo-menu">
          <el-icon><MagicStick /></el-icon><span>3 分钟演示中心</span><b>演示</b>
        </el-menu-item>

        <p class="menu-caption">服务现场</p>
        <el-sub-menu index="group-service">
          <template #title><el-icon><ChatDotRound /></el-icon><span>客户服务</span></template>
          <el-menu-item index="/widget/preview"><span>客户入口预览</span></el-menu-item>
          <el-menu-item index="/agent/console"><span>坐席工作台</span></el-menu-item>
          <el-menu-item v-if="userStore.role === 'admin'" index="/session/monitor"><span>会话监控</span></el-menu-item>
          <el-menu-item v-if="userStore.role === 'admin'" index="/widget/config"><span>Widget 品牌与渠道</span></el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="userStore.role === 'admin'" index="group-knowledge">
          <template #title><el-icon><Collection /></el-icon><span>可信知识</span></template>
          <el-menu-item index="/knowledge/list"><span>文档与版本</span></el-menu-item>
          <el-menu-item index="/knowledge/quality"><span>知识质量中心</span></el-menu-item>
          <el-menu-item index="/knowledge/search-test"><span>回答验证台</span></el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="group-business">
          <template #title><el-icon><Tickets /></el-icon><span>工单与订单</span></template>
          <el-menu-item index="/ticket/list"><span>工单看板</span></el-menu-item>
          <el-menu-item index="/order/list"><span>订单履约</span></el-menu-item>
          <el-menu-item index="/order/refunds"><span>退款审批</span></el-menu-item>
        </el-sub-menu>

        <template v-if="userStore.role === 'admin'">
          <p class="menu-caption">运营与治理</p>
          <el-menu-item index="/analytics/overview"><el-icon><DataAnalysis /></el-icon><span>运营成效</span></el-menu-item>
          <el-menu-item index="/agent-report"><el-icon><Notebook /></el-icon><span>运营分析助手</span></el-menu-item>
          <el-sub-menu index="group-billing">
            <template #title><el-icon><TrendCharts /></el-icon><span>用量与账单</span></template>
            <el-menu-item index="/billing/usage"><span>用量概览</span></el-menu-item>
            <el-menu-item index="/billing/invoices"><span>账单列表</span></el-menu-item>
          </el-sub-menu>

          <p class="menu-caption">系统</p>
          <el-sub-menu index="group-system">
            <template #title><el-icon><Setting /></el-icon><span>配置与治理</span></template>
            <el-menu-item index="/setup"><span>配置向导</span></el-menu-item>
            <el-menu-item index="/onboarding/members"><span>成员邀请</span></el-menu-item>
            <el-menu-item index="/settings"><span>规则配置</span></el-menu-item>
            <el-menu-item index="/audit/logs"><span>审计日志</span></el-menu-item>
            <el-menu-item index="/platform/operations"><span>平台运营入口</span></el-menu-item>
          </el-sub-menu>
        </template>
      </el-menu>

      <div class="sidebar-footer" :class="{ degraded: tenantStore.degradation_alert }">
        <span class="signal-pulse"></span>
        <span><strong>{{ tenantStore.degradation_alert ? '检索降级' : '检索正常' }}</strong><small>{{ tenantStore.degradation_alert ? '知识回答可能不完整' : '知识 · 业务 · 人工' }}</small></span>
      </div>
    </el-aside>

    <el-container class="content-shell">
      <el-header class="admin-header">
        <div class="header-left">
          <button type="button" class="nav-toggle" :aria-expanded="navOpen" aria-label="打开导航" @click="navOpen = !navOpen"><el-icon><Menu /></el-icon></button>
          <span class="page-context">{{ currentPage.eyebrow }}</span>
          <strong>{{ currentPage.title }}</strong>
        </div>
        <div class="header-right">
          <el-tag v-if="tenantStore.plan_type" size="small" effect="plain">{{ PLAN_LABEL[tenantStore.plan_type] || tenantStore.plan_type }}</el-tag>
          <el-popover placement="bottom-end" :width="360" trigger="click" @show="loadNotifications">
            <template #reference>
              <el-badge :value="unreadCount" :hidden="unreadCount === 0" class="notif-badge"><button class="icon-button" aria-label="通知"><el-icon><Bell /></el-icon></button></el-badge>
            </template>
            <div v-loading="notifLoading" class="notif-panel">
              <div class="notif-panel-head"><strong>服务提醒</strong><small>{{ unreadCount }} 条未读</small></div>
              <div v-if="notifications.length === 0" class="notif-empty">当前没有待处理提醒</div>
              <button v-for="item in notifications" :key="item.id" type="button" class="notif-item" :class="{ unread: !item.read_at }" @click="readNotification(item)">
                <span class="notif-title">{{ item.title }}</span><span class="notif-content">{{ item.content }}</span><span class="notif-time">{{ formatNotifTime(item.created_at) }}</span>
              </button>
            </div>
          </el-popover>
          <el-dropdown @command="handleCommand">
            <span class="user-trigger"><span class="user-avatar">{{ userStore.role === 'admin' ? '管' : '席' }}</span><span class="user-copy"><strong>{{ userStore.role === 'admin' ? '管理员' : '坐席' }}</strong><small>{{ userStore.phone || '当前账号' }}</small></span><el-icon class="caret"><ArrowDown /></el-icon></span>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="profile"><el-icon><User /></el-icon> 个人中心</el-dropdown-item><el-dropdown-item command="logout"><el-icon><SwitchButton /></el-icon> 退出登录</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="admin-main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Odometer, Collection, Tickets, TrendCharts, MagicStick, Notebook, ChatDotRound, Setting, User, ArrowDown, SwitchButton, Bell, DataAnalysis, Menu } from '@element-plus/icons-vue'
import { getNotifications, getUnreadCount, markNotificationRead, type NotificationItem } from '@/api/notification'
import { useTenantStore } from '@/store/modules/tenant'
import { useUserStore } from '@/store/modules/user'

const PLAN_LABEL: Record<string, string> = { trial: '试用', basic: '基础版', pro: '专业版', enterprise: '企业版' }
const PAGE_META: Array<[string, string, string]> = [
  ['/demo-center', '引导演示', '3 分钟演示中心'], ['/agent/console', '正在服务', '坐席工作台'],
  ['/knowledge/quality', '知识状况', '知识质量中心'], ['/knowledge', '可信知识', '可信知识'],
  ['/analytics', '运营', '运营成效'], ['/agent-report', '运营助手', '运营分析助手'],
  ['/order', '订单履约', '订单与退款'], ['/ticket', '工单处理', '工单处理'],
  ['/widget', '客户入口', '客户入口'], ['/setup', '上线准备', '配置向导'],
]

const tenantStore = useTenantStore()
const userStore = useUserStore()
const router = useRouter()
const route = useRoute()
const currentPage = computed(() => {
  const match = PAGE_META.find(([prefix]) => route.path.startsWith(prefix))
  return match ? { eyebrow: match[1], title: match[2] } : { eyebrow: '今日服务', title: '服务驾驶舱' }
})

const navOpen = ref(false)
watch(() => route.path, () => { navOpen.value = false })
const notifications = ref<NotificationItem[]>([])
const unreadCount = ref(0)
const notifLoading = ref(false)
function formatNotifTime(iso: string) { return new Date(iso).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }
async function refreshUnread() { try { unreadCount.value = (await getUnreadCount()).data.count } catch { /* fail open */ } }
async function loadNotifications() { notifLoading.value = true; try { notifications.value = (await getNotifications({ page_size: 10 })).data.items; await refreshUnread() } finally { notifLoading.value = false } }
async function readNotification(item: NotificationItem) { if (item.read_at) return; try { await markNotificationRead(item.id); item.read_at = new Date().toISOString(); unreadCount.value = Math.max(0, unreadCount.value - 1) } catch (error: any) { ElMessage.error(error?.message || '标为已读失败') } }
function handleCommand(command: string) { if (command === 'logout') handleLogout(); if (command === 'profile') router.push('/profile') }
async function handleLogout() { await userStore.logout(); router.push('/login') }

onMounted(async () => {
  try { await tenantStore.fetchHome() } catch (error: any) { if (error?.status !== 401) ElMessage.error(error?.message || '加载企业空间失败') }
  refreshUnread()
})
</script>

<style scoped>
.admin-layout { min-height: 100vh; background: var(--zh-bg-page); }
.nav-toggle { display: none; }.nav-backdrop { position: fixed; inset: 0; z-index: 30; background: rgba(8, 18, 22, .46); border: 0; }
.sidebar { display: flex; flex-direction: column; overflow: hidden; color: #c5d3d5; background: var(--zh-bg-sidebar); border-right: 1px solid #1b333a; }
.logo { height: 68px; display: flex; align-items: center; gap: 11px; padding: 0 19px; flex: 0 0 auto; }
.logo-core { display: grid; place-items: center; width: 34px; height: 34px; color: #08201f; font-weight: 850; background: var(--zh-signal); border-radius: 10px 10px 10px 3px; }
.logo-copy { display: grid; line-height: 1.2; }.logo-copy strong { color: #fff; font-size: 15px; }.logo-copy small { margin-top: 3px; color: #678187; font-size: 12px; letter-spacing: .04em; }
.workspace-chip { display: flex; align-items: center; gap: 10px; margin: 2px 12px 10px; padding: 10px 11px; background: rgba(255,255,255,.045); border: 1px solid rgba(255,255,255,.07); border-radius: 11px; }
.workspace-dot { width: 8px; height: 8px; background: var(--zh-signal); border-radius: 50%; box-shadow: 0 0 0 5px rgba(24,183,160,.09); }
.workspace-chip > span:last-child { display: grid; min-width: 0; }.workspace-chip small { color: #6f898e; font-size: 9px; }.workspace-chip strong { overflow: hidden; color: #dce7e8; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.side-menu { flex: 1; overflow-y: auto; padding: 3px 9px 16px; background: transparent; border: 0; --el-menu-bg-color: transparent; --el-menu-text-color: #91a8ac; --el-menu-hover-bg-color: rgba(255,255,255,.055); --el-menu-active-color: #a8f2e5; --el-menu-item-height: 42px; --el-menu-sub-item-height: 38px; }
.side-menu :deep(.el-menu-item), .side-menu :deep(.el-sub-menu__title) { margin: 2px 0; border-radius: 9px; font-size: 12px; }
.side-menu :deep(.el-menu-item.is-active) { background: linear-gradient(90deg, rgba(24,183,160,.17), rgba(24,183,160,.06)); box-shadow: inset 2px 0 0 var(--zh-signal); }
.menu-caption { margin: 15px 10px 6px; color: #8aa0a4; font-size: 12px; font-weight: 700; letter-spacing: .04em; }
.demo-menu b { margin-left: auto; padding: 2px 4px; color: #0b2924; font-size: 7px; background: #72dbc9; border-radius: 4px; }
.sidebar-footer { display: flex; align-items: center; gap: 10px; margin: 10px 12px 14px; padding: 11px; border-top: 1px solid rgba(255,255,255,.08); }
.signal-pulse { width: 8px; height: 8px; background: #67d8a3; border-radius: 50%; box-shadow: 0 0 0 5px rgba(103,216,163,.08); animation: pulse 2.5s infinite; }.sidebar-footer.degraded .signal-pulse { background: var(--zh-coral); box-shadow: 0 0 0 5px rgba(233,101,85,.12); }.sidebar-footer > span:last-child { display: grid; }.sidebar-footer strong { color: #bdd0d3; font-size: 10px; }.sidebar-footer small { color: #5d777c; font-size: 8px; }
.content-shell { min-width: 0; }.admin-header { height: 68px; display: flex; align-items: center; justify-content: space-between; padding: 0 clamp(18px, 2.5vw, 36px); background: rgba(255,254,250,.9); border-bottom: 1px solid var(--zh-border-light); backdrop-filter: blur(14px); }
.header-left { display: grid; }.nav-toggle { place-items: center; width: 34px; height: 34px; margin-bottom: 6px; color: var(--zh-text-regular); background: #f6f8f6; border: 1px solid var(--zh-border-light); border-radius: 10px; cursor: pointer; }.nav-toggle:focus-visible, .icon-button:focus-visible { outline: 2px solid var(--zh-primary); outline-offset: 2px; }.page-context { color: var(--zh-primary); font-size: 12px; font-weight: 700; letter-spacing: .02em; }.header-left strong { margin-top: 3px; font-size: 16px; }
.header-right { display: flex; align-items: center; gap: 14px; }.icon-button { display: grid; place-items: center; width: 34px; height: 34px; color: var(--zh-text-regular); background: #f6f8f6; border: 1px solid var(--zh-border-light); border-radius: 10px; cursor: pointer; }
.user-trigger { display: flex; align-items: center; gap: 9px; cursor: pointer; outline: none; }.user-avatar { display: grid; place-items: center; width: 33px; height: 33px; color: #fff; font-size: 11px; font-weight: 800; background: var(--zh-ink-soft); border-radius: 10px; }.user-copy { display: grid; line-height: 1.15; }.user-copy strong { font-size: 11px; }.user-copy small { max-width: 100px; overflow: hidden; margin-top: 3px; color: var(--zh-text-secondary); font-size: 8px; text-overflow: ellipsis; }.caret { color: var(--zh-text-secondary); font-size: 10px; }
.admin-main { overflow-y: auto; padding: 0; background: transparent; }
.notif-panel-head { display: flex; justify-content: space-between; padding: 3px 4px 11px; border-bottom: 1px solid var(--zh-border-light); }.notif-panel-head small { color: var(--zh-text-secondary); }.notif-item { display: grid; width: 100%; padding: 11px 6px; text-align: left; background: transparent; border: 0; border-bottom: 1px solid var(--zh-border-light); cursor: pointer; }.notif-item.unread { box-shadow: inset 2px 0 0 var(--zh-primary); }.notif-title { font-size: 12px; font-weight: 700; }.notif-content { margin-top: 3px; color: var(--zh-text-regular); font-size: 11px; }.notif-time { margin-top: 5px; color: var(--zh-text-placeholder); font-size: 9px; }.notif-empty { padding: 32px; color: var(--zh-text-secondary); text-align: center; }
@keyframes pulse { 50% { box-shadow: 0 0 0 9px rgba(103,216,163,0); } }
@media (max-width: 850px) {
  .nav-toggle { display: grid; }
  .header-left { grid-template-columns: auto 1fr; column-gap: 10px; align-items: center; }
  .nav-toggle { grid-row: 1 / span 2; margin: 0; }
  .sidebar { position: fixed; z-index: 40; top: 0; bottom: 0; left: 0; width: min(280px, 86vw) !important; transform: translateX(-105%); transition: transform .18s ease; }
  .sidebar.is-open { transform: none; }
}
@media (prefers-reduced-motion: reduce) { .sidebar, .signal-pulse { transition: none; animation: none; } }
</style>
