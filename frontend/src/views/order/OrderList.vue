<template>
  <div>
    <PageHeader title="订单列表" subtitle="查看所有订单及支付/履约状态" />
    <div class="page-container">
      <el-card shadow="never">
        <!-- 013 审查 P1-6：按履约状态分栏看板（PRD 3.6/3.8） -->
        <el-tabs v-model="activeStatus" @tab-change="loadOrders">
          <el-tab-pane v-for="g in statusGroups" :key="g.value" :name="g.value">
            <template #label>{{ g.label }}（{{ countBy(g.value) }}）</template>
          </el-tab-pane>
        </el-tabs>
        <el-table :data="orders" v-loading="loading" stripe>
          <el-table-column prop="order_no" label="订单号" width="140" />
          <el-table-column label="金额" width="120"><template #default="{ row }">¥{{ row.amount.toFixed(2) }}</template></el-table-column>
          <el-table-column label="实付" width="120"><template #default="{ row }">¥{{ row.paid_amount.toFixed(2) }}</template></el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="tracking_no" label="物流单号" width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ row.tracking_no || '-' }}</template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="180"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button v-if="row.status === 'paid'" type="primary" size="small" @click="openShip(row)">发货</el-button>
              <span v-else>-</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 013 US6: 发货弹窗 -->
      <el-dialog v-model="shipDialog" title="订单发货" width="420px">
        <p>订单号：{{ shipRow?.order_no }}</p>
        <el-input v-model="trackingNo" placeholder="请输入物流单号" />
        <template #footer>
          <el-button @click="shipDialog = false">取消</el-button>
          <el-button type="primary" :loading="shipping" @click="confirmShip">确认发货</el-button>
        </template>
      </el-dialog>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { getOrderList, shipOrder, ORDER_STATUS_MAP, type Order } from '@/api/order'
import PageHeader from '@/components/common/PageHeader.vue'

const orders = ref<Order[]>([])
const loading = ref(false)
const shipDialog = ref(false)
const shipRow = ref<Order | null>(null)
const trackingNo = ref('')
const shipping = ref(false)
const activeStatus = ref('')

/** 履约状态分栏（全部 + 主要状态） */
const statusGroups = [
  { value: '', label: '全部' },
  { value: 'paid', label: '已支付' },
  { value: 'shipped', label: '已发货' },
  { value: 'completed', label: '已完成' },
  { value: 'refunding', label: '退款中' },
  { value: 'refunded', label: '已退款' },
  { value: 'cancelled', label: '已取消' },
]

/** 看板分栏数量（基于当前已加载列表） */
function countBy(status: string): number {
  return status ? orders.value.filter((o) => o.status === status).length : orders.value.length
}

function statusType(status: string): string {
  return { completed: 'success', refunded: 'success', cancelled: 'info', refunding: 'warning', paid: 'primary', shipped: 'primary' }[status] || 'info'
}
function statusLabel(status: string): string {
  return (ORDER_STATUS_MAP as Record<string, string>)[status] || status
}
function formatDate(iso: string): string { return new Date(iso).toLocaleString('zh-CN') }

async function loadOrders() {
  loading.value = true
  try {
    // 看板模式拉取当前分栏数据；演示规模一次拉取
    const res = await getOrderList({ page: 1, page_size: 200 })
    let items = res.data.items
    if (activeStatus.value) items = items.filter((o) => o.status === activeStatus.value)
    orders.value = items
  } catch (e: any) { ElMessage.error(e?.message || '加载失败') } finally { loading.value = false }
}

function openShip(row: Order) { shipRow.value = row; trackingNo.value = ''; shipDialog.value = true }

async function confirmShip() {
  if (!shipRow.value) return
  if (!trackingNo.value.trim()) { ElMessage.warning('请输入物流单号'); return }
  shipping.value = true
  try {
    await shipOrder(shipRow.value.id, trackingNo.value.trim())
    ElMessage.success('发货成功')
    shipDialog.value = false
    await loadOrders()
  } catch (e: any) {
    if (e?.status === 400) ElMessage.error('当前状态不允许发货')
    else ElMessage.error(e?.message || '发货失败')
  } finally { shipping.value = false }
}

onMounted(loadOrders)
</script>
<style scoped>
</style>
