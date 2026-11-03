<template>
  <div class="ticket-page">
    <PageHeader title="工单看板" subtitle="从待处理到关闭，每一张工单都有清晰的下一步">
      <template #actions>
        <el-button @click="loadTickets">刷新</el-button>
        <el-button type="primary" @click="openCreate">新建工单</el-button>
      </template>
    </PageHeader>

    <div class="page-container">
      <section class="workflow-summary" aria-label="工单状态总览">
        <button
          v-for="item in statusGroups.slice(1)"
          :key="item.value"
          type="button"
          class="workflow-card surface-card"
          :class="[{ active: activeStatus === item.value }, `tone-${item.value}`]"
          @click="changeStatus(item.value)"
        >
          <span class="status-dot" />
          <span><strong>{{ item.label }}</strong><small>{{ item.note }}</small></span>
          <b class="mono-data">{{ countBy(item.value) }}</b>
        </button>
      </section>

      <section class="ticket-board surface-card">
        <header class="board-head">
          <div>
            <p class="eyebrow">待办队列</p>
            <h2>{{ activeLabel }}</h2>
            <span>{{ total }} 张工单 · 按最新创建排序</span>
          </div>
          <el-input v-model="keyword" clearable placeholder="搜索工单号或标题" aria-label="搜索工单" class="board-search" />
        </header>

        <el-tabs v-model="activeStatus" class="status-tabs" @tab-change="handleTabChange">
          <el-tab-pane v-for="group in statusGroups" :key="group.value" :name="group.value">
            <template #label>{{ group.label }} <span class="tab-count">{{ countBy(group.value) }}</span></template>
          </el-tab-pane>
        </el-tabs>

        <el-table v-loading="loading" :data="filteredTickets" row-key="id" class="ticket-table">
          <el-table-column label="工单" min-width="250">
            <template #default="{ row }">
              <button type="button" class="ticket-identity" @click="showDetail(row)">
                <span class="mono-data">{{ row.ticket_no }}</span>
                <strong>{{ row.title }}</strong>
              </button>
            </template>
          </el-table-column>
          <el-table-column label="优先级" width="104">
            <template #default="{ row }"><el-tag :type="priorityType(row.priority)" size="small" effect="light">{{ PRIORITY_MAP[row.priority] || row.priority }}</el-tag></template>
          </el-table-column>
          <el-table-column label="当前阶段" width="120">
            <template #default="{ row }"><span class="status-pill" :class="`status-${row.status}`"><i />{{ STATUS_MAP[row.status] || row.status }}</span></template>
          </el-table-column>
          <el-table-column label="等待时间" width="120">
            <template #default="{ row }"><span :class="{ overdue: isOverdue(row) }">{{ formatAge(row.created_at) }}</span></template>
          </el-table-column>
          <el-table-column label="来源" width="90">
            <template #default="{ row }">{{ creatorLabel(row.created_by) }}</template>
          </el-table-column>
          <el-table-column label="下一步" width="190" fixed="right">
            <template #default="{ row }">
              <el-button v-if="nextStatus(row.status)" type="primary" size="small" :loading="transitioning === row.id" @click="advance(row)">{{ nextActionLabel(row.status) }}</el-button>
              <el-tag v-else type="success" effect="plain">流程已完成</el-tag>
              <el-button link size="small" @click="showDetail(row)">详情</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <div class="empty-action">
              <div><strong>{{ keyword ? '没有匹配的工单' : '这个阶段暂时没有工单' }}</strong><p>{{ keyword ? '换一个工单号或标题试试。' : '服务队列很干净，可以查看其他阶段。' }}</p><el-button v-if="activeStatus || keyword" plain type="primary" @click="clearFilters">查看全部工单</el-button></div>
            </div>
          </template>
        </el-table>

        <div class="pagination-wrap"><el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" @current-change="loadTickets" /></div>
      </section>
    </div>

    <el-drawer v-model="drawerVisible" title="工单处理详情" size="min(480px, 92vw)" direction="rtl">
      <div v-loading="detailLoading" class="detail-body">
        <template v-if="currentTicket">
          <p class="eyebrow mono-data">{{ currentTicket.ticket_no }}</p>
          <h3 class="detail-title">{{ currentTicket.title }}</h3>
          <div class="detail-state">
            <span class="status-pill" :class="`status-${currentTicket.status}`"><i />{{ STATUS_MAP[currentTicket.status] }}</span>
            <el-tag :type="priorityType(currentTicket.priority)" effect="light">{{ PRIORITY_MAP[currentTicket.priority] }}优先级</el-tag>
          </div>

          <div class="ticket-flow" aria-label="工单处理流程">
            <div v-for="(step, index) in flowSteps" :key="step.value" :class="{ done: flowIndex(currentTicket.status) >= index, current: currentTicket.status === step.value }"><span>{{ flowIndex(currentTicket.status) > index ? '✓' : index + 1 }}</span><small>{{ step.label }}</small></div>
          </div>

          <section class="desc-section"><h4>问题描述</h4><div class="desc-content">{{ currentTicket.description || '创建时未填写详细描述。' }}</div></section>
          <dl class="detail-meta"><div><dt>创建来源</dt><dd>{{ creatorLabel(currentTicket.created_by) }}</dd></div><div><dt>创建时间</dt><dd>{{ formatDate(currentTicket.created_at) }}</dd></div><div><dt>已等待</dt><dd>{{ formatAge(currentTicket.created_at) }}</dd></div></dl>

          <div v-if="nextStatus(currentTicket.status)" class="drawer-action">
            <div><strong>建议下一步</strong><p>{{ nextActionHint(currentTicket.status) }}</p></div>
            <el-button type="primary" :loading="transitioning === currentTicket.id" @click="advance(currentTicket)">{{ nextActionLabel(currentTicket.status) }}</el-button>
          </div>
        </template>
      </div>
    </el-drawer>

    <el-dialog v-model="createVisible" title="新建服务工单" width="min(520px, 92vw)">
      <el-form label-position="top">
        <el-form-item label="问题标题" required><el-input v-model="createForm.title" maxlength="120" show-word-limit placeholder="用一句话描述客户问题" /></el-form-item>
        <el-form-item label="详细描述"><el-input v-model="createForm.description" type="textarea" :rows="4" maxlength="1000" show-word-limit placeholder="记录已知信息、客户诉求和需要跟进的事项" /></el-form-item>
        <el-form-item label="处理优先级"><el-segmented v-model="createForm.priority" :options="priorityOptions" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="creating" @click="confirmCreate">创建并进入待处理</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createTicket, getTicketDetail, getTicketList, transitionTicket, type Ticket } from '@/api/ticket'
import PageHeader from '@/components/common/PageHeader.vue'

const STATUS_MAP: Record<string, string> = { open: '待处理', in_progress: '处理中', resolved: '已解决', closed: '已关闭' }
const PRIORITY_MAP: Record<string, string> = { low: '低', medium: '中', high: '高', urgent: '紧急' }
const statusGroups = [
  { value: '', label: '全部', note: '所有阶段' },
  { value: 'open', label: '待处理', note: '需要首次响应' },
  { value: 'in_progress', label: '处理中', note: '正在跟进' },
  { value: 'resolved', label: '已解决', note: '等待确认关闭' },
  { value: 'closed', label: '已关闭', note: '服务已完成' },
] as const
const flowSteps = statusGroups.slice(1)
const priorityOptions = [{ label: '低', value: 'low' }, { label: '中', value: 'medium' }, { label: '高', value: 'high' }, { label: '紧急', value: 'urgent' }]

const tickets = ref<Ticket[]>([])
const counts = ref<Record<string, number>>({})
const loading = ref(false)
const activeStatus = ref<'' | Ticket['status']>('')
const keyword = ref('')
const page = ref(1)
const pageSize = 10
const total = ref(0)
const transitioning = ref<number | null>(null)
const drawerVisible = ref(false)
const detailLoading = ref(false)
const currentTicket = ref<Ticket | null>(null)
const createVisible = ref(false)
const creating = ref(false)
const createForm = ref({ title: '', description: '', priority: 'medium' })

const activeLabel = computed(() => statusGroups.find((item) => item.value === activeStatus.value)?.label || '全部工单')
const filteredTickets = computed(() => {
  const needle = keyword.value.trim().toLowerCase()
  if (!needle) return tickets.value
  return tickets.value.filter((item) => `${item.ticket_no} ${item.title}`.toLowerCase().includes(needle))
})

function countBy(status: string) {
  if (!status) return Object.values(counts.value).reduce((sum, value) => sum + value, 0) || total.value
  return counts.value[status] || 0
}
function priorityType(priority: string) { return ({ urgent: 'danger', high: 'danger', medium: 'warning', low: 'info' } as Record<string, string>)[priority] || 'info' }
function creatorLabel(value?: string) { return ({ agent: 'AI 创建', staff: '人工创建', message: '客户留言' } as Record<string, string>)[value || ''] || value || '未知' }
function formatDate(iso: string) { return new Date(iso).toLocaleString('zh-CN') }
function ageHours(iso: string) { return Math.max(0, (Date.now() - new Date(iso).getTime()) / 3600000) }
function formatAge(iso: string) { const hours = ageHours(iso); if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} 分钟`; if (hours < 24) return `${Math.round(hours)} 小时`; return `${Math.round(hours / 24)} 天` }
function isOverdue(ticket: Ticket) { return ['open', 'in_progress'].includes(ticket.status) && ageHours(ticket.created_at) >= (ticket.priority === 'urgent' ? 1 : ticket.priority === 'high' ? 4 : 24) }
function flowIndex(status: string) { return flowSteps.findIndex((step) => step.value === status) }
function nextStatus(status: Ticket['status']): Ticket['status'] | null { return ({ open: 'in_progress', in_progress: 'resolved', resolved: 'closed' } as Partial<Record<Ticket['status'], Ticket['status']>>)[status] || null }
function nextActionLabel(status: Ticket['status']) { return ({ open: '开始处理', in_progress: '标记解决', resolved: '关闭工单' } as Partial<Record<Ticket['status'], string>>)[status] || '已完成' }
function nextActionHint(status: Ticket['status']) { return ({ open: '接手后工单会进入处理中，团队能看到当前负责人正在跟进。', in_progress: '确认客户问题已经处理完成，再标记为已解决。', resolved: '客户结果已确认后关闭工单，结束本次服务。' } as Partial<Record<Ticket['status'], string>>)[status] || '' }

async function loadTickets() {
  loading.value = true
  try {
    const response = await getTicketList({ page: page.value, page_size: pageSize, status: activeStatus.value || undefined })
    tickets.value = response.data.items
    total.value = response.data.total
    counts.value = response.data.status_counts || tickets.value.reduce<Record<string, number>>((acc, item) => { acc[item.status] = (acc[item.status] || 0) + 1; return acc }, {})
  } catch (error: any) { ElMessage.error(error?.message || '工单加载失败') }
  finally { loading.value = false }
}
function changeStatus(status: string) { activeStatus.value = status as typeof activeStatus.value; page.value = 1; loadTickets() }
function handleTabChange(value: string | number) { changeStatus(String(value)) }
function clearFilters() { keyword.value = ''; changeStatus('') }

async function showDetail(row: Ticket) {
  drawerVisible.value = true
  currentTicket.value = row
  detailLoading.value = true
  try { currentTicket.value = (await getTicketDetail(row.id)).data }
  catch { ElMessage.warning('完整详情暂时不可用，已展示当前概要') }
  finally { detailLoading.value = false }
}
async function advance(row: Ticket) {
  const target = nextStatus(row.status)
  if (!target) return
  if (target === 'closed') {
    try { await ElMessageBox.confirm('确认客户结果已经得到验证，并关闭这张工单？', '关闭工单', { confirmButtonText: '确认关闭', cancelButtonText: '再检查一下' }) }
    catch { return }
  }
  transitioning.value = row.id
  try {
    await transitionTicket(row.id, target)
    ElMessage.success(`工单已更新为“${STATUS_MAP[target]}”`)
    if (currentTicket.value?.id === row.id) currentTicket.value = { ...currentTicket.value, status: target }
    await loadTickets()
  } catch (error: any) { ElMessage.error(error?.message || '工单状态更新失败，请刷新后重试') }
  finally { transitioning.value = null }
}
function openCreate() { createForm.value = { title: '', description: '', priority: 'medium' }; createVisible.value = true }
async function confirmCreate() {
  if (!createForm.value.title.trim()) { ElMessage.warning('请填写问题标题'); return }
  creating.value = true
  try {
    await createTicket({ title: createForm.value.title.trim(), description: createForm.value.description.trim() || undefined, priority: createForm.value.priority, client_request_id: `ticket-ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` })
    ElMessage.success('工单已创建并进入待处理队列')
    createVisible.value = false
    changeStatus('open')
  } catch (error: any) { ElMessage.error(error?.message || '工单创建失败') }
  finally { creating.value = false }
}

onMounted(loadTickets)
</script>

<style scoped lang="scss">
.workflow-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
.workflow-card { display: grid; grid-template-columns: auto 1fr auto; gap: 11px; align-items: center; min-height: 88px; padding: 17px; color: var(--zh-text-primary); text-align: left; cursor: pointer; transition: transform .18s, border-color .18s, box-shadow .18s; }
.workflow-card:hover { transform: translateY(-2px); box-shadow: var(--zh-shadow-hover); }.workflow-card.active { border-color: var(--zh-primary); box-shadow: 0 0 0 2px var(--zh-primary-bg); }.status-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--zh-amber); box-shadow: 0 0 0 5px var(--zh-amber-bg); }.tone-in_progress .status-dot { background: var(--zh-primary); box-shadow: 0 0 0 5px var(--zh-primary-bg); }.tone-resolved .status-dot { background: var(--zh-signal); box-shadow: 0 0 0 5px var(--zh-primary-bg); }.tone-closed .status-dot { background: #8fa09d; box-shadow: 0 0 0 5px #edf1ef; }.workflow-card > span:nth-child(2) { display: grid; }.workflow-card strong { font-size: 14px; }.workflow-card small { margin-top: 4px; color: var(--zh-text-secondary); font-size: 12px; }.workflow-card b { font-size: 28px; }
.ticket-board { padding: 21px; }.board-head { display: flex; justify-content: space-between; gap: 20px; align-items: flex-end; }.board-head h2 { margin: 5px 0 4px; font-size: 21px; }.board-head > div > span { color: var(--zh-text-secondary); font-size: 12px; }.board-search { width: 260px; }.status-tabs { margin-top: 14px; }.tab-count { display: inline-grid; place-items: center; min-width: 21px; height: 19px; margin-left: 4px; padding: 0 5px; font-size: 11px; background: #edf2ef; border-radius: 99px; }
.ticket-identity { display: grid; gap: 4px; padding: 0; color: inherit; text-align: left; background: none; border: 0; cursor: pointer; }.ticket-identity span { color: var(--zh-primary); font-size: 11px; }.ticket-identity strong { max-width: 360px; overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }.status-pill { display: inline-flex; gap: 7px; align-items: center; font-size: 12px; font-weight: 650; }.status-pill i { width: 7px; height: 7px; background: var(--zh-amber); border-radius: 50%; }.status-in_progress i { background: var(--zh-primary); }.status-resolved i { background: var(--zh-signal); }.status-closed i { background: #97a6a3; }.overdue { color: var(--zh-coral); font-weight: 700; }.empty-action p { margin: 7px 0 14px; font-size: 12px; }
.detail-title { margin: 7px 0 12px; font-size: 22px; line-height: 1.45; }.detail-state { display: flex; gap: 9px; align-items: center; }.ticket-flow { display: grid; grid-template-columns: repeat(4, 1fr); margin: 28px 0; }.ticket-flow > div { position: relative; display: grid; gap: 7px; justify-items: center; color: var(--zh-text-placeholder); font-size: 11px; }.ticket-flow > div::before { position: absolute; top: 13px; right: 50%; width: 100%; border-top: 2px solid var(--zh-border); content: ''; z-index: 0; }.ticket-flow > div:first-child::before { display: none; }.ticket-flow span { position: relative; z-index: 1; display: grid; place-items: center; width: 27px; height: 27px; background: #edf1ef; border-radius: 50%; }.ticket-flow .done span { color: #fff; background: var(--zh-primary); }.ticket-flow .done::before { border-color: var(--zh-primary); }.ticket-flow .current small { color: var(--zh-text-primary); font-weight: 700; }.desc-section h4 { margin-bottom: 9px; font-size: 14px; }.desc-content { min-height: 94px; padding: 14px; color: var(--zh-text-regular); line-height: 1.75; white-space: pre-wrap; background: #f5f7f5; border-radius: 10px; }.detail-meta { margin-top: 20px; }.detail-meta div { display: flex; justify-content: space-between; padding: 10px 0; border-top: 1px solid var(--zh-border-light); font-size: 12px; }.detail-meta dt { color: var(--zh-text-secondary); }.drawer-action { display: grid; grid-template-columns: 1fr auto; gap: 14px; align-items: center; margin-top: 24px; padding: 16px; background: var(--zh-primary-bg); border-radius: 12px; }.drawer-action strong { font-size: 13px; }.drawer-action p { margin-top: 4px; color: var(--zh-text-regular); font-size: 11px; line-height: 1.55; }
@media (max-width: 900px) { .workflow-summary { grid-template-columns: repeat(2, 1fr); }.board-head { align-items: stretch; flex-direction: column; }.board-search { width: 100%; } }
@media (max-width: 540px) { .workflow-summary { grid-template-columns: 1fr 1fr; }.workflow-card { grid-template-columns: auto 1fr; padding: 13px; }.workflow-card b { grid-column: 2; font-size: 22px; }.ticket-board { padding: 14px; } }
@media (prefers-reduced-motion: reduce) { .workflow-card { transition: none; } }
</style>
