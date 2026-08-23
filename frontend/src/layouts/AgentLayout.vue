<!-- frontend/src/layouts/AgentLayout.vue -->
<!-- 职责: 坐席工作台布局 - 侧边菜单 + 顶栏(客服身份/个人中心/退出) + 主内容区 -->
<!-- : 菜单全部指向 /agent/* 内聚路由，不再跳回 AdminLayout 管理端路径 -->
<!--  收尾三: 顶栏展示当前客服身份；个人中心走 /agent/profile（复用 Profile.vue，
     留在 AgentLayout）；退出走 userStore.logout() 整体清理（不单删 token），回 /login -->
<template>
  <el-container class="agent-layout">
    <el-aside width="220px" class="sidebar">
      <div class="brand">智枢 · 坐席工作台</div>
      <el-menu :default-active="$route.path" router background-color="#001529" text-color="#c8d0da" active-text-color="#fff">
        <el-menu-item index="/agent/console">
          <span>会话处理</span>
        </el-menu-item>
        <el-menu-item index="/agent/tickets">
          <span>工单看板</span>
        </el-menu-item>
        <el-menu-item index="/agent/orders">
          <span>订单列表</span>
        </el-menu-item>
        <el-menu-item index="/agent/refunds">
          <span>退款审批</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="agent-header">
        <div class="header-left">坐席工作台</div>
        <div class="header-right">
          <el-dropdown @command="handleCommand">
            <span class="user-trigger" data-test="agent-user-trigger">
              <el-icon><User /></el-icon>
              <span>{{ userStore.phone || '客服' }}</span>
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
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { User, ArrowDown, SwitchButton } from '@element-plus/icons-vue'
import { useUserStore } from '@/store/modules/user'

const userStore = useUserStore()
const router = useRouter()

/** 下拉命令分发: 个人中心留在 AgentLayout 内的 /agent/profile；退出走整体登出 */
function handleCommand(cmd: string) {
  if (cmd === 'profile') router.push('/agent/profile')
  if (cmd === 'logout') handleLogout()
}

/** 退出登录: userStore.logout() 无条件清 token/角色/租户等全部本地态，再回 /login */
async function handleLogout() {
  await userStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.agent-layout { height: 100vh; }
.sidebar { background: #001529; color: #fff; }
.brand { padding: 16px; font-size: 16px; font-weight: 600; color: #fff; }
.sidebar :deep(.el-menu) { border-right: none; }
.agent-header {
  display: flex; align-items: center; justify-content: space-between;
  background: #fff; border-bottom: 1px solid #e4e7ed; height: 56px; padding: 0 24px;
}
.header-left { font-size: 15px; font-weight: 600; color: #303133; }
.user-trigger {
  display: flex; align-items: center; gap: 6px; cursor: pointer;
  color: #606266; outline: none; padding: 6px 10px; border-radius: 4px;
}
.user-trigger:hover { background: #ecf5ff; color: #409eff; }
.caret { font-size: 12px; }
.main { background: #f5f7fa; }
</style>
