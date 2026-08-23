<!-- 规则配置：退款审批阈值（PRD §3.6）+ 租户数据导出（PRD §8.5），仅管理员 -->
<template>
  <div>
    <PageHeader title="规则配置" subtitle="退款审批阈值与数据导出（仅管理员）" />
    <div class="page-container">
      <el-card shadow="never" class="settings-card">
        <template #header><span>退款审批阈值（PRD 3.6）</span></template>
        <p class="hint">单笔退款金额超过该阈值时，需企业管理员二次审批。</p>
        <el-form label-position="top" @submit.prevent="handleSave">
          <el-form-item label="大额退款审批阈值（元）">
            <el-input-number
              v-model="threshold"
              :min="0.01"
              :max="1000000"
              :precision="2"
              :step="100"
              style="width: 240px"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card shadow="never" class="settings-card">
        <template #header><span>租户数据导出（PRD 8.5）</span></template>
        <p class="hint">导出知识库、会话、工单、订单、退款审批、账单与用量数据（CSV 打包 ZIP）。</p>
        <el-button type="primary" plain :loading="exporting" @click="handleExport">导出全部数据 (ZIP)</el-button>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import PageHeader from '@/components/common/PageHeader.vue'
import { getRefundThreshold, updateRefundThreshold, exportTenantData } from '@/api/tenant'

const threshold = ref(1000)
const saving = ref(false)
const exporting = ref(false)

onMounted(async () => {
  try {
    const res = await getRefundThreshold()
    threshold.value = Number(res.data.refund_approval_threshold) || 1000
  } catch (e: any) {
    ElMessage.error(e?.message || '加载阈值失败')
  }
})

async function handleSave() {
  saving.value = true
  try {
    await updateRefundThreshold(threshold.value)
    ElMessage.success('退款审批阈值已更新')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleExport() {
  exporting.value = true
  try {
    const res = await exportTenantData()
    const blob = new Blob([res.data], { type: 'application/zip' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'zhihub-tenant-export.zip'
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('导出已开始下载')
  } catch (e: any) {
    ElMessage.error(e?.message || '导出失败')
  } finally {
    exporting.value = false
  }
}
</script>

<style scoped>
.settings-card { max-width: 720px; margin-bottom: 16px; }
.hint { color: #909399; font-size: 13px; margin: 0 0 12px; }
</style>
