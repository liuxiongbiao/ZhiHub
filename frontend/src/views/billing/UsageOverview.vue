<!-- frontend/src/views/billing/UsageOverview.vue -->
<!-- 职责: 用量概览 - 进度条/80%/100%预警展示(规格 012 前端打磨:PageHeader + el-card) -->
<template>
  <div>
    <PageHeader title="用量概览" subtitle="本月对话用量与配额使用情况" />

    <div class="page-container">
      <div class="export-bar">
        <el-button size="small" :loading="exporting" @click="handleExport">导出用量明细 (CSV)</el-button>
      </div>
      <el-card shadow="never" class="quota-card">
        <template v-if="quota">
          <div class="quota-row">
            <span class="quota-label">本月对话用量</span>
            <span class="quota-count">{{ quota.count }} / {{ quota.quota }}</span>
          </div>

          <el-progress
            :percentage="percent"
            :status="percent >= 100 ? 'exception' : percent >= 80 ? 'warning' : 'success'"
            :stroke-width="18"
            :format="formatPercent"
          />

          <div class="quota-remaining">剩余配额：{{ quota.remaining }} 次</div>

          <el-alert
            v-if="percent >= 100"
            title="配额已用尽 - 超额部分将按量计费（不会中断服务）"
            type="error"
            show-icon
            :closable="false"
            class="quota-alert"
          />
          <el-alert
            v-else-if="percent >= 80"
            title="配额使用已达 80% - 请留意超额费用"
            type="warning"
            show-icon
            :closable="false"
            class="quota-alert"
          />
        </template>
        <div v-else v-loading="true" class="quota-loading" />
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getQuota, exportUsageDetail, type Quota } from '@/api/billing'
import PageHeader from '@/components/common/PageHeader.vue'

const quota = ref<Quota | null>(null)
const exporting = ref(false)

/** 配额使用百分比（用量/配额×100） */
const percent = computed(() => {
  if (!quota.value || quota.value.quota === 0) return 0
  return Math.round((quota.value.count / quota.value.quota) * 100)
})

/** 进度条百分比格式化 → "N%" */
function formatPercent(p: number): string {
  return `${p}%`
}

onMounted(async () => {
  try {
    const res = await getQuota()
    quota.value = res.data
  } catch (e: any) {
    // 静默失败
  }
})

/** 导出用量与 Token 消耗明细 CSV（PRD §3.9：会话ID/时间/意图/模型/Token/是否超额） */
async function handleExport() {
  exporting.value = true
  try {
    const res = await exportUsageDetail()
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = 'zhihub-usage-detail.csv'
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已导出用量明细')
  } catch (e: any) {
    ElMessage.error(e?.message || '导出失败')
  } finally { exporting.value = false }
}
</script>

<style scoped lang="scss">
.export-bar { margin-bottom: var(--zh-gap); }
.quota-card { max-width: 640px; }
.quota-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--zh-gap);
}
.quota-label { font-size: 15px; color: var(--zh-text-regular); }
.quota-count { font-size: 18px; font-weight: 600; color: var(--zh-text-primary); }
.quota-remaining { margin-top: var(--zh-gap); color: var(--zh-text-secondary); font-size: 13px; }
.quota-alert { margin-top: var(--zh-gap); }
.quota-loading { height: 120px; }
</style>
