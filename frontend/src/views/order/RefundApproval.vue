<!-- frontend/src/views/order/RefundApproval.vue -->
<!-- 职责: 售后审批列表 - 待审批高亮(012 前端打磨:PageHeader + el-card) -->
<template>
  <div>
    <PageHeader title="退款审批" subtitle="审核用户提交的退款申请" />

    <div class="page-container">
      <el-card shadow="never">
        <el-table :data="refunds" v-loading="loading" stripe>
          <el-table-column prop="id" label="审批ID" width="80" />
          <el-table-column prop="order_id" label="订单ID" width="90" />
          <el-table-column label="退款金额" width="120">
            <template #default="{ row }">¥{{ row.amount.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="原因" min-width="180" show-overflow-tooltip />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" size="small">{{ STATUS_MAP[row.status] || row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="申请时间" width="180">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="140">
            <template #default="{ row }">
              <template v-if="row.status === 'pending'">
                <el-button link type="success" size="small" @click="handleApprove(row, 'approved')">批准</el-button>
                <el-button link type="danger" size="small" @click="handleApprove(row, 'rejected')">拒绝</el-button>
                <el-button link type="warning" size="small" @click="handleCancel(row)">撤销</el-button>
              </template>
              <span v-else class="muted">已处理</span>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-wrap">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="total, prev, pager, next"
            @current-change="loadRefunds"
          />
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getRefundList, approveRefund, cancelRefund, type RefundApproval } from '@/api/order'
import PageHeader from '@/components/common/PageHeader.vue'

/** 退款审批状态 -> 中文标签 */
const STATUS_MAP: Record<string, string> = {
  pending: '待审批', approved: '已批准', rejected: '已拒绝',
  refunding: '退款中', refunded: '已退款', cancelled: '已撤销',
}

const refunds = ref<RefundApproval[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 10
const total = ref(0)

/** 审批状态 -> el-tag 配色 */
function statusType(status: string): string {
  return { pending: 'warning', approved: 'success', rejected: 'danger', refunding: 'warning', refunded: 'success', cancelled: 'info' }[status] || 'info'
}

/** ISO 时间串 -> 中文本地化时间 */
function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN')
}

/** 拉取退款审批列表（分页），失败提示 */
async function loadRefunds() {
  loading.value = true
  try {
    const res = await getRefundList({ page: page.value, page_size: pageSize })
    refunds.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

/** 审批退款申请：二次确认后调 approveRefund */
async function handleApprove(row: RefundApproval, decision: 'approved' | 'rejected') {
  const label = decision === 'approved' ? '批准' : '拒绝'
  try {
    await ElMessageBox.confirm(`确认${label}退款申请 #${row.id}（¥${row.amount.toFixed(2)}）？`, '审批确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await approveRefund(row.id, decision)
    ElMessage.success(`${label}成功`)
    loadRefunds()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

/** 撤销售后申请（仅 pending 可撤销，013 US6） */
async function handleCancel(row: RefundApproval) {
  try {
    await ElMessageBox.confirm(`确认撤销售后申请 #${row.id}（¥${row.amount.toFixed(2)}）？`, '撤销确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await cancelRefund(row.id)
    ElMessage.success('已撤销')
    loadRefunds()
  } catch (e: any) {
    ElMessage.error(e?.message || '撤销失败')
  }
}

onMounted(loadRefunds)
</script>

<style scoped lang="scss">
.muted { color: var(--zh-text-placeholder); font-size: 12px; }
</style>
