/**
 * src/layouts/AdminLayout.vue
 *
 * 职责: 企业管理后台布局 —— 左侧导航菜单、顶部用户区与内容区；
 *      菜单按角色渲染，路由切换经 vue-router，未读提醒经站内信 API 轮询。
 *
 * 依赖: userStore（当前用户/退出登录）、tenantStore（租户信息）、notification API（未读提醒）。
 */
<template>
  <el-container class="admin-layout">
    <el-aside width="220px" class="sidebar">
      <div class="logo">
        <el-icon class="logo-icon"><Service /></el-icon>
        <span class="logo-text">智枢 ZhiHub</span>
      </div>
      <el-menu
        router
        :default-active="$route.path"
        class="side-menu"
        :collapse="false"
        unique-opened
      >
        <el-menu-item index="/">
          <el-icon><Odometer /></el-icon>
          <span>工作台</span>
        </el-menu-item>
        <el-menu-item index="/widget/preview">
          <el-icon><ChatDotRound /></el-icon>
          <span>Widget 预览</span>
        </el-menu-item>
        <el-menu-item v-if="userStore.role === 'admin'" index="/widget/config">
          <el-icon><Brush /></el-icon>
          <span>品牌定制</span>
        </el-menu-item>

        <el-sub-menu v-if="userStore.role === 'admin'" index="group-knowledge">
          <template #title>
            <el-icon><Collection /></el-icon>
            <span>知识管理</span>
          </template>
          <el-menu-item index="/knowledge/list">
            <el-icon><Files /></el-icon>
            <span>知识库</span>
          </el-menu-item>
          <el-menu-item index="/knowledge/search-test">
            <el-icon><Search /></el-icon>
            <span>检索测试台</span>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="group-business">
          <template #title>
            <el-icon><Tickets /></el-icon>
            <span>工单与订单</span>
          </template>
          <el-menu-item index="/ticket/list">
            <el-icon><Document /></el-icon>
            <span>工单看板</span>
          </el-menu-item>
          <el-menu-item v-if="userStore.role === 'admin'" index="/session/monitor">
            <el-icon><ChatDotRound /></el-icon>
            <span>会话监控</span>
          </el-menu-item>
          <el-menu-item index="/order/list">
            <el-icon><List /></el-icon>
            <span>订单列表</span>
          </el-menu-item>
          <el-menu-item index="/order/refunds">
            <el-icon><Wallet /></el-icon>
            <span>退款审批</span>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="userStore.role === 'admin'" index="group-billing">
          <template #title>
            <el-icon><TrendCharts /></el-icon>
            <span>计费</span>
          </template>
          <el-menu-item index="/billing/usage">
            <el-icon><DataLine /></el-icon>
            <span>用量概览</span>
          </el-menu-item>
          <el-menu-item index="/billing/invoices">
            <el-icon><Money /></el-icon>
            <span>账单列表</span>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="group-agent">
          <template #title>
            <el-icon><MagicStick /></el-icon>
            <span>智能体</span>
          </template>
          <el-menu-item index="/agent-report">
            <el-icon><Notebook /></el-icon>
            <span>DeepAgent 报告</span>
          </el-menu-item>
          <el-menu-item index="/agent/console">
            <el-icon><Headset /></el-icon>
            <span>坐席工作台</span>
          </el-menu-item>
        </el-sub-menu>

        <el-menu-item v-if="userStore.role === 'admin'" index="/setup">
          <el-icon><Setting /></el-icon>
          <span>配置向导</span>
        </el-menu-item>
        <el-menu-item v-if="userStore.role === 'admin'" index="/settings">
          <el-icon><Tools /></el-icon>
          <span>规则配置</span>
        </el-menu-item>
        <el-menu-item v-if="userStore.role === 'admin'" index="/audit/logs">
          <el-icon><DocumentChecked /></el-icon>
          <span>审计日志</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="admin-header">
        <div class="header-left">
          <span class="tenant-name">{{ tenantStore.tenant_name || '加载中...' }}</span>
          <el-tag v-if="tenantStore.plan_type" size="small" type="primary" effect="light">
            {{ PLAN_LABEL[tenantStore.plan_type] || tenantStore.plan_type }}
          </el-tag>
        </div>
        <div class="header-right">
          <el-popover placement="bottom-end" :width="340" trigger="click" @show="loadNotifications">
            <template #reference>
              <el-badge :value="unreadCount" :hidden="unreadCount === 0" class="notif-badge">
                <el-icon class="notif-icon"><Bell /></el-icon>
              </el-badge>
            </template>
            <div v-loading="notifLoading" class="notif-panel">
              <div v-if="notifications.length === 0" class="notif-empty">暂无通知</div>
              <div v-for="n in notifications" :key="n.id" class="notif-item" :class="{ unread: !n.read_at }" @click="readNotification(n)">
                <div class="notif-title">{{ n.title }}</div>
                <div class="notif-content">{{ n.content }}</div>
                <div class="notif-time">{{ formatNotifTime(n.created_at) }}</div>
              </div>
            </div>
          </el-popover>
          <el-dropdown @command="handleCommand">
            <span class="user-trigger">
              <el-icon><User /></el-icon>
              <span>{{ userStore.phone || '管理员' }}</span>
              <el-icon class="caret"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon> 个人中心
                </el-dropdown-item>
                <el-dropdown-item command="logout">
                  <el-icon><SwitchButton /></el-icon> 退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="admin-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<!--
  AdminLayout.vue - 后台管理布局(规格 012 前端打磨)

  结构:
    el-container
    ├─ el-aside(浅色侧边栏 220px)
    │   ├─ .logo(Service 图标 + 智枢 ZhiHub)
    │   └─ el-menu router(分组 sub-menu + 图标 + unique-opened)
    └─ el-container
        ├─ el-header(左:租户名+套餐徽标; 右:用户下拉退出)
        └─ el-main(浅灰背景,衬托白色卡片)

  设计: 浅色侧边栏 + Element Plus 默认蓝主色,现代克制。
  菜单分组: 工作台 / 知识管理 / 工单与订单 / 计费 / 智能体。
-->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Service, Odometer, Collection, Files, Search, Tickets, Document, List,
  Wallet, TrendCharts, DataLine, Money, MagicStick, Notebook, Headset,
  ChatDotRound, Setting, Tools, User, ArrowDown, SwitchButton, Brush, DocumentChecked, Bell,
} from '@element-plus/icons-vue'
import { getNotifications, getUnreadCount, markNotificationRead, type NotificationItem } from '@/api/notification'
import { useTenantStore } from '@/store/modules/tenant'
import { useUserStore } from '@/store/modules/user'

/** 套餐类型 → 中文标签 */
const PLAN_LABEL: Record<string, string> = {
  trial: '试用', basic: '基础版', pro: '专业版', enterprise: '企业版',
}

const tenantStore = useTenantStore()
const userStore = useUserStore()
const router = useRouter()

onMounted(async () => {
  try {
    await tenantStore.fetchHome()
  } catch (e: any) {
    if (e?.status !== 401) {
      ElMessage.error(e?.message || '加载租户信息失败')
    }
  }
})


const notifications = ref<NotificationItem[]>([])
const unreadCount = ref(0)
const notifLoading = ref(false)

function formatNotifTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN')
}

async function refreshUnread() {
  try {
    const res = await getUnreadCount()
    unreadCount.value = res.data.count
  } catch { /* 静默 */ }
}

async function loadNotifications() {
  notifLoading.value = true
  try {
    const res = await getNotifications({ page_size: 10 })
    notifications.value = res.data.items
    await refreshUnread()
  } catch { /* 静默 */ } finally { notifLoading.value = false }
}

async function readNotification(n: NotificationItem) {
  if (n.read_at) return
  try {
    await markNotificationRead(n.id)
    n.read_at = new Date().toISOString()
    unreadCount.value = Math.max(0, unreadCount.value - 1)
  } catch { /* 静默 */ }
}

onMounted(() => {
  refreshUnread()
  // 组合现有轮询节奏刷新未读（简化：初始+麃铃展开时刷新）
})

/** 下拉菜单命令分发: logout → 退出登录 */
function handleCommand(cmd: string) {
  if (cmd === 'logout') handleLogout()
  if (cmd === 'profile') router.push('/profile')
}

/** 退出登录: 清除用户状态并跳转登录页 */
async function handleLogout() {
  await userStore.logout()
  router.push('/login')
}
</script>

<style scoped lang="scss">
.admin-layout { min-height: 100vh; }

/* 侧边栏:浅色基调,右侧细分隔线 */
.sidebar {
  background: var(--zh-bg-sidebar);
  border-right: 1px solid var(--zh-border-light);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Logo 区 */
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  border-bottom: 1px solid var(--zh-border-light);
  flex-shrink: 0;
}
.logo-icon {
  font-size: 24px;
  color: var(--zh-primary);
}
.logo-text {
  font-size: 17px;
  font-weight: 700;
  color: var(--zh-text-primary);
  letter-spacing: 0.5px;
}

/* 菜单:去掉默认右边框(侧边栏已有分隔线),撑满高度 */
.side-menu {
  border-right: none;
  flex: 1;
  padding: 8px;
  overflow-y: auto;
}

/* 顶栏 */
.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--zh-bg-card);
  border-bottom: 1px solid var(--zh-border-light);
  box-shadow: var(--zh-shadow);
  height: 60px;
  padding: 0 24px;
}
.header-left { display: flex; align-items: center; gap: 12px; }
.tenant-name { font-size: 16px; font-weight: 600; color: var(--zh-text-primary); }

/* 用户下拉 */
.user-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: var(--zh-text-regular);
  outline: none;
  padding: 6px 10px;
  border-radius: var(--zh-radius-sm);
  transition: background 0.2s;
}
.user-trigger:hover { background: var(--zh-primary-bg); color: var(--zh-primary); }
.caret { font-size: 12px; }

/* 内容区:浅灰背景衬托卡片 */
.admin-main {
  background: var(--zh-bg-page);
  padding: 0;
  overflow-y: auto;
}
</style>
