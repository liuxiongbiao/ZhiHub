<!-- frontend/src/components/common/PageHeader.vue -->
<!--
  职责: 页面顶部标题栏复用组件,统一所有业务页面的 header 样式
  设计: 左侧标题+副标题,右侧操作区(slot),底部细分隔线

  用法:
    <PageHeader title="工单看板" subtitle="查看与处理所有工单">
      <template #actions>
        <el-select ... />
        <el-button ... />
      </template>
    </PageHeader>
-->
<template>
  <div class="page-header">
    <div class="page-header__left">
      <h2 class="page-header__title">{{ title }}</h2>
      <p v-if="subtitle" class="page-header__subtitle">{{ subtitle }}</p>
    </div>
    <div class="page-header__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<style scoped lang="scss">
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  background: var(--zh-bg-card);
  border-bottom: 1px solid var(--zh-border-light);
}
.page-header__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--zh-text-primary);
  margin: 0;
  line-height: 1.4;
}
.page-header__subtitle {
  font-size: 13px;
  color: var(--zh-text-secondary);
  margin-top: 4px;
}
.page-header__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
</style>
