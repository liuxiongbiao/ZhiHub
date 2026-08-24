<template>
  <el-container class="agent-layout">
    <el-aside width="92px" class="sidebar">
      <div class="brand"><span>枢</span><small>坐席</small></div>
      <el-menu :default-active="$route.path" router class="agent-menu">
        <el-menu-item index="/agent/console"><el-icon><Headset /></el-icon><span>会话</span></el-menu-item>
        <el-menu-item index="/agent/tickets"><el-icon><Tickets /></el-icon><span>工单</span></el-menu-item>
        <el-menu-item index="/agent/orders"><el-icon><List /></el-icon><span>订单</span></el-menu-item>
        <el-menu-item index="/agent/refunds"><el-icon><Wallet /></el-icon><span>退款</span></el-menu-item>
      </el-menu>
      <div class="rail-status"><i></i><small>在线</small></div>
    </el-aside>
    <el-container class="agent-shell">
      <el-header class="agent-header">
        <div class="header-left"><span class="eyebrow">SERVICE DESK</span><strong>{{ currentTitle }}</strong></div>
        <div class="header-right">
          <span class="shift-chip"><i></i>本班次在线</span>
          <el-dropdown @command="handleCommand"><span class="user-trigger" data-test="agent-user-trigger"><span class="avatar">席</span><span><strong>当前客服</strong><small>{{ userStore.phone || '客服账号' }}</small></span><el-icon class="caret"><ArrowDown /></el-icon></span><template #dropdown><el-dropdown-menu><el-dropdown-item command="profile"><el-icon><User /></el-icon> 个人中心</el-dropdown-item><el-dropdown-item command="logout"><el-icon><SwitchButton /></el-icon> 退出登录</el-dropdown-item></el-dropdown-menu></template></el-dropdown>
        </div>
      </el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { User, ArrowDown, SwitchButton, Headset, Tickets, List, Wallet } from '@element-plus/icons-vue'
import { useUserStore } from '@/store/modules/user'
const userStore = useUserStore()
const router = useRouter()
const route = useRoute()
const currentTitle = computed(() => ({ '/agent/console': '会话处理', '/agent/tickets': '工单看板', '/agent/orders': '订单履约', '/agent/refunds': '退款审批', '/agent/profile': '个人中心' } as Record<string,string>)[route.path] || '坐席工作台')
function handleCommand(command: string) { if (command === 'profile') router.push('/agent/profile'); if (command === 'logout') handleLogout() }
async function handleLogout() { await userStore.logout(); router.push('/login') }
</script>

<style scoped>
.agent-layout { height: 100vh; background: var(--zh-bg-page); }.sidebar { display: flex; flex-direction: column; align-items: stretch; overflow: hidden; color: #fff; background: var(--zh-ink); }.brand { display: grid; justify-items: center; gap: 5px; padding: 17px 0 15px; }.brand > span { display: grid; place-items: center; width: 34px; height: 34px; color: #08201f; font-weight: 850; background: var(--zh-signal); border-radius: 10px 10px 10px 3px; }.brand small { color: #718b90; font-size: 8px; letter-spacing: .18em; }.agent-menu { flex: 1; padding: 8px; background: transparent; border: 0; --el-menu-bg-color: transparent; --el-menu-text-color: #789096; --el-menu-active-color: #a9f0e4; --el-menu-hover-bg-color: rgba(255,255,255,.06); }.agent-menu :deep(.el-menu-item) { height: 58px; display: flex; flex-direction: column; justify-content: center; gap: 3px; margin: 5px 0; padding: 0 !important; border-radius: 12px; line-height: 1; }.agent-menu :deep(.el-menu-item.is-active) { background: rgba(24,183,160,.14); box-shadow: inset 2px 0 0 var(--zh-signal); }.agent-menu :deep(.el-icon) { margin: 0; font-size: 18px; }.agent-menu :deep(span) { font-size: 8px; }.rail-status { display: grid; justify-items: center; gap: 5px; padding: 15px 0; color: #769095; }.rail-status i { width: 7px; height: 7px; background: #68d49f; border-radius: 50%; box-shadow: 0 0 0 5px rgba(104,212,159,.08); }.rail-status small { font-size: 7px; }.agent-shell { min-width: 0; }.agent-header { height: 62px; display: flex; align-items: center; justify-content: space-between; padding: 0 22px; background: rgba(255,254,250,.92); border-bottom: 1px solid var(--zh-border-light); }.header-left { display: grid; }.header-left .eyebrow { font-size: 7px; }.header-left strong { margin-top: 2px; font-size: 14px; }.header-right { display: flex; gap: 17px; align-items: center; }.shift-chip { display: flex; gap: 7px; align-items: center; color: var(--zh-text-secondary); font-size: 8px; }.shift-chip i { width: 6px; height: 6px; background: var(--zh-signal); border-radius: 50%; }.user-trigger { display: flex; gap: 8px; align-items: center; cursor: pointer; outline: none; }.avatar { display: grid; place-items: center; width: 31px; height: 31px; color: #fff; font-size: 10px; font-weight: 800; background: var(--zh-ink-soft); border-radius: 9px; }.user-trigger > span:nth-child(2) { display: grid; }.user-trigger strong { font-size: 9px; }.user-trigger small { max-width: 100px; overflow: hidden; color: var(--zh-text-secondary); font-size: 7px; text-overflow: ellipsis; }.caret { color: var(--zh-text-secondary); font-size: 9px; }.main { min-width: 0; padding: 0; overflow: auto; background: transparent; }
@media (max-width: 700px) { .sidebar { width: 68px !important; }.brand small, .agent-menu :deep(span), .rail-status small { display: none; }.shift-chip { display: none; } }
</style>
