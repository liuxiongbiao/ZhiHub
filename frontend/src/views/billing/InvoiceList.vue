<!-- frontend/src/views/billing/InvoiceList.vue -->
<!-- 职责: 账单列表 - 支付状态 + 支付入口（规格 013 US2） -->
<template>
  <div>
    <PageHeader title="账单列表" subtitle="查看历史账单与支付状态" />

    <div class="page-container">
      <el-card shadow="never">
        <el-table :data="invoices" v-loading="loading" stripe>
          <el-table-column prop="period" label="账期" width="120" />
          <el-table-column label="基础费" width="120">
            <template #default="{ row }">¥{{ row.base_fee.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="超额费" width="120">
            <template #default="{ row }">¥{{ row.overage_fee.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="合计" width="120">
            <template #default="{ row }">
              <span class="amount-total">¥{{ (row.base_fee + row.overage_fee).toFixed(2) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.status === 'paid' ? 'success' : 'danger'" size="small">
                {{ row.status === 'paid' ? '已支付' : '未支付' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'unpaid'"
                type="primary"
                size="small"
                :loading="payingId === row.id"
                @click="handlePay(row)"
              >支付</el-button>
              <span v-else class="paid-text">—</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getInvoices, payInvoice, type Invoice } from '@/api/billing'
import PageHeader from '@/components/common/PageHeader.vue'

const invoices = ref<Invoice[]>([])
const loading = ref(false)
const payingId = ref<number | null>(null)

async function loadInvoices() {
  loading.value = true
  try {
    const res = await getInvoices()
    invoices.value = res.data.items || []
  } catch (e: any) {
    ElMessage.error(e?.message || '加载账单失败')
  } finally { loading.value = false }
}

onMounted(loadInvoices)

async function handlePay(row: Invoice) {
  try {
    await ElMessageBox.confirm(`确认支付账单 #${row.id}（¥${(row.base_fee + row.overage_fee).toFixed(2)}）？将跳转支付宝沙箱。`, '支付确认', {
      type: 'warning',
      confirmButtonText: '去支付',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  payingId.value = row.id
  try {
    const res = await payInvoice(row.id)
    const payUrl = res.data.pay_url
    if (payUrl) {
      ElMessage.success('正在跳转支付页面...')
      window.location.href = payUrl
    } else {
      ElMessage.success('支付已发起')
      // 无跳转 URL（demo 模式）时刷新账单状态，避免手动刷新
      await loadInvoices()
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '支付失败')
  } finally { payingId.value = null }
}
</script>

<style scoped lang="scss">
.amount-total { font-weight: 600; color: var(--zh-text-primary); }
.paid-text { color: #c0c4cc; }
</style>
