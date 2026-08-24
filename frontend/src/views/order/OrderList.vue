<template>
  <div class="order-page">
    <PageHeader title="订单列表" subtitle="把支付、发货和售后放在同一条履约线上">
      <template #actions><el-button @click="loadOrders">刷新</el-button><el-button type="primary" @click="openCreate">新建订单</el-button></template>
    </PageHeader>

    <div class="page-container">
      <section class="fulfillment-strip surface-card" aria-label="订单履约概览">
        <div class="strip-title"><p class="eyebrow">FULFILLMENT FLOW</p><strong>订单履约信号</strong><small>优先处理需要人工动作的订单</small></div>
        <button type="button" :class="{ active: activeStatus === 'paid' }" @click="changeStatus('paid')"><span class="signal pay">¥</span><span><small>等待发货</small><strong class="mono-data">{{ countBy('paid') }}</strong></span><b>去处理 →</b></button>
        <button type="button" :class="{ active: activeStatus === 'shipped' }" @click="changeStatus('shipped')"><span class="signal ship">↗</span><span><small>运输途中</small><strong class="mono-data">{{ countBy('shipped') }}</strong></span><b>看物流 →</b></button>
        <button type="button" :class="{ active: activeStatus === 'refund_requested' }" @click="changeStatus('refund_requested')"><span class="signal refund">!</span><span><small>待退款审批</small><strong class="mono-data">{{ countBy('refund_requested') }}</strong></span><b>需关注 →</b></button>
        <button type="button" :class="{ active: activeStatus === 'completed' }" @click="changeStatus('completed')"><span class="signal done">✓</span><span><small>履约完成</small><strong class="mono-data">{{ countBy('completed') }}</strong></span><b>已完成</b></button>
      </section>

      <section class="order-board surface-card">
        <header class="board-head">
          <div><p class="eyebrow">ORDER OPERATIONS</p><h2>{{ activeLabel }}</h2><span>共 {{ total }} 条 · 金额只展示，不在列表页修改</span></div>
          <el-input v-model="keyword" clearable placeholder="搜索订单号或物流单号" aria-label="搜索订单" class="board-search" />
        </header>

        <el-tabs v-model="activeStatus" class="status-tabs" @tab-change="handleTabChange">
          <el-tab-pane v-for="group in statusGroups" :key="group.value" :name="group.value"><template #label>{{ group.label }} <span class="tab-count">{{ countBy(group.value) }}</span></template></el-tab-pane>
        </el-tabs>

        <el-table v-loading="loading" :data="filteredOrders" row-key="id">
          <el-table-column label="订单" min-width="180">
            <template #default="{ row }"><div class="order-identity"><strong class="mono-data">{{ row.order_no }}</strong><small>{{ creatorLabel(row.created_by) }} · {{ formatDate(row.created_at) }}</small></div></template>
          </el-table-column>
          <el-table-column label="金额" width="126"><template #default="{ row }"><div class="money-cell"><strong class="mono-data">{{ money(row.paid_amount) }}</strong><small>订单 {{ money(row.amount) }}</small></div></template></el-table-column>
          <el-table-column label="履约状态" width="132"><template #default="{ row }"><span class="order-status" :class="`order-${row.status}`"><i />{{ statusLabel(row.status) }}</span></template></el-table-column>
          <el-table-column label="物流 / 售后进度" min-width="180">
            <template #default="{ row }"><span v-if="row.tracking_no" class="tracking mono-data">{{ row.tracking_no }}</span><span v-else-if="row.status === 'paid'" class="action-hint">等待录入物流单号</span><span v-else-if="row.status === 'refund_requested'" class="action-hint refund-hint">等待管理员审批</span><span v-else class="muted">暂无物流信息</span></template>
          </el-table-column>
          <el-table-column label="可执行操作" width="210" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.status === 'paid'" type="primary" size="small" @click="openShip(row)">发货</el-button>
              <el-button v-if="canRefund(row)" plain size="small" @click="openRefund(row)">申请退款</el-button>
              <el-tag v-if="!canRefund(row) && row.status !== 'paid'" :type="terminalStatus(row.status) ? 'success' : 'info'" effect="plain">{{ terminalStatus(row.status) ? '流程已完成' : '无需操作' }}</el-tag>
            </template>
          </el-table-column>
          <template #empty><div class="empty-action"><div><strong>{{ keyword ? '没有匹配的订单' : '这个阶段暂时没有订单' }}</strong><p>{{ keyword ? '换一个订单号或物流单号试试。' : '可以查看其他履约阶段或创建测试订单。' }}</p><el-button v-if="activeStatus || keyword" plain type="primary" @click="clearFilters">查看全部订单</el-button></div></div></template>
        </el-table>

        <div class="pagination-wrap"><el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" @current-change="loadOrders" /></div>
      </section>
    </div>

    <el-dialog v-model="shipDialog" title="订单发货" width="min(440px, 92vw)">
      <div class="dialog-order"><span>订单</span><strong class="mono-data">{{ shipRow?.order_no }}</strong><small>{{ money(shipRow?.paid_amount || 0) }}</small></div>
      <el-form label-position="top"><el-form-item label="物流单号" required><el-input v-model="trackingNo" placeholder="请输入物流单号" maxlength="100" @keyup.enter="confirmShip" /></el-form-item></el-form>
      <el-alert type="info" :closable="false" title="确认后订单会进入“已发货”，并在客户查询时返回这个物流单号。" />
      <template #footer><el-button @click="shipDialog = false">取消</el-button><el-button type="primary" :loading="shipping" @click="confirmShip">确认发货</el-button></template>
    </el-dialog>

    <el-dialog v-model="refundDialog" title="发起退款申请" width="min(500px, 92vw)">
      <div class="dialog-order"><span>订单</span><strong class="mono-data">{{ refundRow?.order_no }}</strong><small>最多可退 {{ money(refundRow?.paid_amount || 0) }}</small></div>
      <el-form label-position="top">
        <el-form-item label="退款金额" required><el-input-number v-model="refundForm.amount" :min="0.01" :max="Number(refundRow?.paid_amount || 0)" :precision="2" controls-position="right" /></el-form-item>
        <el-form-item label="订单绑定手机号或尾号" required><el-input v-model="refundForm.phone" maxlength="20" placeholder="输入完整手机号，或至少后 4 位" /></el-form-item>
        <el-form-item label="退款原因"><el-input v-model="refundForm.reason" type="textarea" :rows="3" maxlength="300" show-word-limit placeholder="记录客户诉求，便于审批人员判断" /></el-form-item>
      </el-form>
      <el-alert type="warning" :closable="false" title="系统会校验手机号尾号和实付上限；提交后进入退款审批，不会直接打款。" />
      <template #footer><el-button @click="refundDialog = false">取消</el-button><el-button type="primary" :loading="refunding" @click="confirmRefund">提交审批</el-button></template>
    </el-dialog>

    <el-dialog v-model="createDialog" title="新建订单" width="min(500px, 92vw)">
      <el-form label-position="top">
        <el-form-item label="订单号" required><el-input v-model="createForm.order_no" placeholder="例如 ORD-20260824-001" maxlength="80" /></el-form-item>
        <div class="form-grid"><el-form-item label="订单金额" required><el-input-number v-model="createForm.amount" :min="0.01" :precision="2" controls-position="right" /></el-form-item><el-form-item label="实付金额"><el-input-number v-model="createForm.paid_amount" :min="0" :precision="2" controls-position="right" /></el-form-item></div>
        <el-form-item label="绑定手机号"><el-input v-model="createForm.phone" placeholder="可选；用于退款身份校验" maxlength="20" /></el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" title="手工创建的订单初始为“待支付”，支付状态应由正式支付回调更新。" />
      <template #footer><el-button @click="createDialog = false">取消</el-button><el-button type="primary" :loading="creating" @click="confirmCreate">创建订单</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createOrder, getOrderList, requestRefund, shipOrder, ORDER_STATUS_MAP, type Order, type OrderStatus } from '@/api/order'
import PageHeader from '@/components/common/PageHeader.vue'

const statusGroups: Array<{ value: '' | OrderStatus; label: string }> = [
  { value: '', label: '全部' }, { value: 'created', label: '待支付' }, { value: 'paid', label: '已支付' },
  { value: 'shipped', label: '已发货' }, { value: 'completed', label: '已完成' },
  { value: 'refund_requested', label: '待退款审批' }, { value: 'refunding', label: '退款中' },
  { value: 'refunded', label: '已退款' }, { value: 'cancelled', label: '已取消' },
]
const orders = ref<Order[]>([])
const counts = ref<Partial<Record<OrderStatus, number>>>({})
const loading = ref(false)
const activeStatus = ref<'' | OrderStatus>('')
const keyword = ref('')
const page = ref(1)
const pageSize = 20
const total = ref(0)
const shipDialog = ref(false)
const shipRow = ref<Order | null>(null)
const trackingNo = ref('')
const shipping = ref(false)
const refundDialog = ref(false)
const refundRow = ref<Order | null>(null)
const refundForm = ref({ amount: 0, phone: '', reason: '' })
const refunding = ref(false)
const createDialog = ref(false)
const creating = ref(false)
const createForm = ref({ order_no: '', amount: 0, paid_amount: 0, phone: '' })

const activeLabel = computed(() => statusGroups.find((item) => item.value === activeStatus.value)?.label || '全部订单')
const filteredOrders = computed(() => {
  const needle = keyword.value.trim().toLowerCase()
  if (!needle) return orders.value
  return orders.value.filter((item) => `${item.order_no} ${item.tracking_no || ''}`.toLowerCase().includes(needle))
})
function countBy(status: string) { if (!status) return Object.values(counts.value).reduce((sum, value) => sum + Number(value || 0), 0) || total.value; return Number(counts.value[status as OrderStatus] || 0) }
function statusLabel(status: OrderStatus) { return ORDER_STATUS_MAP[status] || status }
function creatorLabel(value?: string) { return ({ agent: 'AI 创建', staff: '人工创建' } as Record<string, string>)[value || ''] || value || '系统创建' }
function formatDate(iso: string) { return new Date(iso).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) }
function money(value: number) { return `¥${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` }
function canRefund(order: Order) { return ['paid', 'shipped'].includes(order.status) && Number(order.paid_amount) > 0 }
function terminalStatus(status: OrderStatus) { return ['completed', 'refunded', 'cancelled'].includes(status) }

async function loadOrders() {
  loading.value = true
  try {
    const response = await getOrderList({ page: page.value, page_size: pageSize, status: activeStatus.value || undefined })
    orders.value = response.data.items
    total.value = response.data.total
    counts.value = response.data.status_counts || orders.value.reduce<Partial<Record<OrderStatus, number>>>((acc, item) => { acc[item.status] = (acc[item.status] || 0) + 1; return acc }, {})
  } catch (error: any) { ElMessage.error(error?.message || '订单加载失败') }
  finally { loading.value = false }
}
function changeStatus(status: string) { activeStatus.value = status as typeof activeStatus.value; page.value = 1; loadOrders() }
function handleTabChange(value: string | number) { changeStatus(String(value)) }
function clearFilters() { keyword.value = ''; changeStatus('') }
function openShip(row: Order) { shipRow.value = row; trackingNo.value = ''; shipDialog.value = true }
function openRefund(row: Order) { refundRow.value = row; refundForm.value = { amount: Number(row.paid_amount), phone: '', reason: '' }; refundDialog.value = true }
function openCreate() { createForm.value = { order_no: '', amount: 0, paid_amount: 0, phone: '' }; createDialog.value = true }

async function confirmShip() {
  if (!shipRow.value) return
  if (!trackingNo.value.trim()) { ElMessage.warning('请输入物流单号'); return }
  shipping.value = true
  try { await shipOrder(shipRow.value.id, trackingNo.value.trim()); ElMessage.success('发货成功，订单已进入运输中'); shipDialog.value = false; await loadOrders() }
  catch (error: any) { ElMessage.error(error?.message || '发货失败，请刷新订单状态后重试') }
  finally { shipping.value = false }
}
async function confirmRefund() {
  if (!refundRow.value) return
  if (!refundForm.value.phone.trim() || refundForm.value.phone.trim().length < 4) { ElMessage.warning('请输入至少 4 位手机号尾号'); return }
  if (refundForm.value.amount <= 0 || refundForm.value.amount > Number(refundRow.value.paid_amount)) { ElMessage.warning('退款金额必须大于 0 且不超过实付金额'); return }
  refunding.value = true
  try {
    await requestRefund({ order_no: refundRow.value.order_no, phone: refundForm.value.phone.trim(), amount: refundForm.value.amount, reason: refundForm.value.reason.trim() || undefined, client_request_id: `refund-ui-${refundRow.value.id}-${Date.now()}` })
    ElMessage.success('退款申请已提交，等待审批')
    refundDialog.value = false
    changeStatus('refund_requested')
  } catch (error: any) { ElMessage.error(error?.message || '退款申请提交失败') }
  finally { refunding.value = false }
}
async function confirmCreate() {
  if (!createForm.value.order_no.trim()) { ElMessage.warning('请输入订单号'); return }
  if (createForm.value.amount <= 0) { ElMessage.warning('订单金额必须大于 0'); return }
  if (createForm.value.paid_amount > createForm.value.amount) { ElMessage.warning('实付金额不能超过订单金额'); return }
  creating.value = true
  try {
    await createOrder({ order_no: createForm.value.order_no.trim(), amount: createForm.value.amount, paid_amount: createForm.value.paid_amount, phone: createForm.value.phone.trim() || undefined })
    ElMessage.success('订单已创建')
    createDialog.value = false
    changeStatus('created')
  } catch (error: any) { ElMessage.error(error?.message || '订单创建失败') }
  finally { creating.value = false }
}
onMounted(loadOrders)
</script>

<style scoped>
.fulfillment-strip { display: grid; grid-template-columns: 1.15fr repeat(4,1fr); overflow: hidden; margin-bottom: 14px; }.strip-title, .fulfillment-strip button { min-height: 110px; padding: 20px; border: 0; border-right: 1px solid var(--zh-border-light); }.strip-title { display: grid; align-content: center; }.strip-title strong { margin: 6px 0 3px; font-size: 18px; }.strip-title small { color: var(--zh-text-secondary); }.fulfillment-strip button { display: grid; grid-template-columns: auto 1fr; gap: 10px; align-content: center; color: var(--zh-text-primary); text-align: left; background: transparent; cursor: pointer; }.fulfillment-strip button:hover, .fulfillment-strip button.active { background: var(--zh-primary-bg); }.fulfillment-strip button > span:nth-child(2) { display: grid; }.fulfillment-strip button small { color: var(--zh-text-secondary); font-size: 11px; }.fulfillment-strip button strong { margin-top: 3px; font-size: 24px; }.fulfillment-strip button b { grid-column: 2; color: var(--zh-primary); font-size: 10px; }.signal { display: grid; place-items: center; width: 32px; height: 32px; color: #fff; font-weight: 800; background: var(--zh-primary); border-radius: 10px; }.signal.refund { background: var(--zh-coral); }.signal.done { background: var(--zh-signal); }.signal.ship { background: var(--zh-ink-soft); }
.order-board { padding: 21px; }.board-head { display: flex; justify-content: space-between; gap: 20px; align-items: flex-end; }.board-head h2 { margin: 5px 0 4px; font-size: 21px; }.board-head > div > span { color: var(--zh-text-secondary); font-size: 12px; }.board-search { width: 280px; }.status-tabs { margin-top: 14px; }.tab-count { display: inline-grid; place-items: center; min-width: 21px; height: 19px; margin-left: 4px; padding: 0 5px; font-size: 11px; background: #edf2ef; border-radius: 99px; }.order-identity, .money-cell { display: grid; gap: 4px; }.order-identity strong { color: var(--zh-primary); font-size: 12px; }.order-identity small, .money-cell small { color: var(--zh-text-secondary); font-size: 11px; }.money-cell strong { font-size: 14px; }.order-status { display: inline-flex; gap: 7px; align-items: center; font-size: 12px; font-weight: 650; }.order-status i { width: 7px; height: 7px; background: #96a5a2; border-radius: 50%; }.order-paid i, .order-shipped i { background: var(--zh-primary); }.order-completed i, .order-refunded i { background: var(--zh-signal); }.order-refund_requested i, .order-refunding i { background: var(--zh-amber); }.tracking { color: var(--zh-primary); font-size: 12px; }.action-hint { color: var(--zh-amber); font-size: 11px; }.refund-hint { color: var(--zh-coral); }.muted { color: var(--zh-text-placeholder); font-size: 11px; }.empty-action p { margin: 7px 0 14px; font-size: 12px; }
.dialog-order { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center; margin-bottom: 20px; padding: 14px; background: #f4f7f5; border-radius: 11px; }.dialog-order span, .dialog-order small { color: var(--zh-text-secondary); font-size: 12px; }.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 1050px) { .fulfillment-strip { grid-template-columns: repeat(2,1fr); }.strip-title { grid-column: 1/-1; min-height: 88px; }.board-head { align-items: stretch; flex-direction: column; }.board-search { width: 100%; } }
@media (max-width: 600px) { .fulfillment-strip { grid-template-columns: 1fr 1fr; }.fulfillment-strip button { padding: 14px; }.order-board { padding: 14px; }.form-grid { grid-template-columns: 1fr; } }
</style>
