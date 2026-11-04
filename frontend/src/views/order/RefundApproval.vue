<!-- frontend/src/views/order/RefundApproval.vue -->
<!-- 职责: 售后审批列表 - 待审批高亮(规格 012 前端打磨:PageHeader + el-card) -->
<template>
  <div>
    <PageHeader title="退款审批" subtitle="审核用户提交的退款申请" />

    <div class="page-container">
      <el-alert
        :title="ruleText"
        type="info"
        :closable="false"
        show-icon
        class="rule-alert"
      />
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
                <el-button link type="success" size="small" @click="handleApprove(row, 'approved')">
                  {{ requiresAdminReview(row) ? '提交复核' : '批准' }}
                </el-button>
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
import { computed, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getRefundList, getRefundRules, approveRefund, cancelRefund, REFUND_STATUS_MAP as STATUS_MAP, type RefundApproval } from '@/api/order'
import PageHeader from '@/components/common/PageHeader.vue'
import { useUserStore } from '@/store/modules/user'
import { formatDateTime as formatDate } from '@/utils/datetime'

const refunds = ref<RefundApproval[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 10
const total = ref(0)
const approvalThreshold = ref(0)
const ruleText = ref('正在加载退款审批额度…')
const userStore = useUserStore()
const isAgent = computed(() => userStore.role === 'agent')

function requiresAdminReview(row: RefundApproval): boolean {
  return isAgent.value && approvalThreshold.value > 0 && row.amount > approvalThreshold.value
}

async function loadRules() {
  try {
    const res = await getRefundRules()
    approvalThreshold.value = res.data.approval_threshold
    ruleText.value = `客服可终审单笔不超过 ¥${approvalThreshold.value.toFixed(2)} 的退款；超额申请将提交管理员复核。`
  } catch {
    ruleText.value = '退款终审额度加载失败，服务端仍会执行额度校验。'
  }
}

/** 审批状态 → el-tag 配色 */
function statusType(status: string): string {
  return { pending: 'warning', approved: 'success', rejected: 'danger', refunding: 'warning', refunded: 'success', cancelled: 'info' }[status] || 'info'
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
    const res = await approveRefund(row.id, decision)
    ElMessage.success(res.data.message || `${label}成功`)
    loadRefunds()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

/** 撤销售后申请（仅 pending 可撤销，规格 013 US6） */
async function handleCancel(row: RefundApproval) {
  let reason = ''
  try {
    const res = await ElMessageBox.prompt(
      `请填写撤销售后申请 #${row.id}（¥${row.amount.toFixed(2)}）的原因`,
      '撤销确认',
      { type: 'warning', inputPlaceholder: '撤销原因（必填）' },
    )
    reason = (res.value || '').trim()
    if (!reason) {
      ElMessage.warning('撤销原因必填')
      return
    }
  } catch {
    return
  }
  try {
    await cancelRefund(row.id, reason)
    ElMessage.success('已撤销')
    loadRefunds()
  } catch (e: any) {
    ElMessage.error(e?.message || '撤销失败')
  }
}

onMounted(() => {
  loadRules()
  loadRefunds()
})
</script>

<style scoped lang="scss">
.muted { color: var(--zh-text-placeholder); font-size: 12px; }
.rule-alert { margin-bottom: 16px; }
</style>
