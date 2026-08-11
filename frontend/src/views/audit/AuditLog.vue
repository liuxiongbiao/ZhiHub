<!-- frontend/src/views/audit/AuditLog.vue -->
<!-- 013: 审计日志（PRD 2.1 系统日志与审计，仅 admin） -->
<template>
  <div>
    <PageHeader title="审计日志" subtitle="关键操作的可追溯记录（仅管理员）" />
    <div class="page-container">
      <el-card shadow="never">
        <el-table :data="logs" v-loading="loading" stripe>
          <el-table-column prop="created_at" label="时间" width="180">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="action" label="操作" width="160" />
          <el-table-column prop="target_type" label="对象类型" width="130" />
          <el-table-column prop="target_id" label="对象 ID" width="100" />
          <el-table-column prop="detail" label="详情" show-overflow-tooltip />
          <el-table-column prop="user_id" label="操作人 ID" width="100" />
        </el-table>
        <div class="pagination-wrap">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="total, prev, pager, next"
            @current-change="loadLogs"
          />
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getAuditLogs, type AuditLogItem } from '@/api/tenant'
import PageHeader from '@/components/common/PageHeader.vue'

const logs = ref<AuditLogItem[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 20
const total = ref(0)

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN')
}

async function loadLogs() {
  loading.value = true
  try {
    const res = await getAuditLogs(page.value, pageSize)
    logs.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    ElMessage.error(e?.message || '加载审计日志失败')
  } finally { loading.value = false }
}

onMounted(loadLogs)
</script>

<style scoped lang="scss">
.pagination-wrap { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>
