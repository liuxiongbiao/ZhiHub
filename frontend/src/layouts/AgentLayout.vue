<!-- frontend/src/layouts/AgentLayout.vue -->
<!-- 职责: 坐席工作台布局 - 侧边菜单 + 主内容区 -->
<template>
  <el-container class="agent-layout">
    <el-aside width="220px" class="sidebar">
      <div class="brand">智枢 · 坐席工作台</div>
      <el-menu :default-active="$route.path" router background-color="#001529" text-color="#c8d0da" active-text-color="#fff">
        <el-menu-item index="/agent/console">
          <span>会话处理</span>
        </el-menu-item>
        <el-menu-item index="/ticket/list">
          <span>工单看板</span>
        </el-menu-item>
        <el-menu-item index="/order/refunds">
          <span>退款审批</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
</script>

<style scoped>
.agent-layout { height: 100vh; }
.sidebar { background: #001529; color: #fff; }
.brand { padding: 16px; font-size: 16px; font-weight: 600; color: #fff; }
.sidebar :deep(.el-menu) { border-right: none; }
.main { background: #f5f7fa; }
</style>
