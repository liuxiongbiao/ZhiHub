<!-- frontend/src/views/ticket/TicketList.vue -->
<!-- 职责: 工单看板 - 列表 + 详情抽屉(012 前端打磨) -->
<template>
  <div>
    <PageHeader title="工单看板" subtitle="查看与跟进所有工单" />

    <div class="page-container">
      <el-card shadow="never">
        <!-- 013 审查 P1-6：按状态分栏看板（PRD 3.8） -->
        <el-tabs v-model="activeStatus" @tab-change="loadTickets">
          <el-tab-pane v-for="g in statusGroups" :key="g.value" :name="g.value">
            <template #label>{{ g.label }}（{{ countBy(g.value) }}）</template>
          </el-tab-pane>
        </el-tabs>
        <el-table :data="tickets" v-loading="loading" stripe>
          <el-table-column prop="ticket_no" label="工单号" width="130" />
          <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" size="small">{{ STATUS_MAP[row.status] || row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="优先级" width="100">
            <template #default="{ row }">
              <el-tag :type="priorityType(row.priority)" size="small">{{ PRIORITY_MAP[row.priority] || row.priority }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="180">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="showDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-wrap">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="total, prev, pager, next"
            @current-change="loadTickets"
          />
        </div>
      </el-card>
    </div>

    <!-- 工单详情抽屉 -->
    <el-drawer v-model="drawerVisible" title="工单详情" size="480px" direction="rtl">
      <div v-loading="detailLoading" class="detail-body">
        <template v-if="currentTicket">
          <h3 class="detail-title">{{ currentTicket.title }}</h3>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="工单号">{{ currentTicket.ticket_no }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusType(currentTicket.status)" size="small">
                {{ STATUS_MAP[currentTicket.status] || currentTicket.status }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="优先级">
              <el-tag :type="priorityType(currentTicket.priority)" size="small">
                {{ PRIORITY_MAP[currentTicket.priority] || currentTicket.priority }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDate(currentTicket.created_at) }}</el-descriptions-item>
          </el-descriptions>

          <div class="desc-section">
            <div class="section-title desc-label">问题描述</div>
            <div class="desc-content">{{ currentTicket.description || '暂无描述' }}</div>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getTicketList, getTicketDetail, type Ticket } from '@/api/ticket'
import PageHeader from '@/components/common/PageHeader.vue'

/** 工单状态 -> 中文标签 */
const STATUS_MAP: Record<string, string> = {
  open: '待处理',
  in_progress: '处理中',
  resolved: '已解决',
  closed: '已关闭',
}
/** 优先级 -> 中文标签 */
const PRIORITY_MAP: Record<string, string> = {
  low: '低', medium: '中', high: '高', urgent: '紧急',
}

const tickets = ref<Ticket[]>([])
const loading = ref(false)
const activeStatus = ref('')
const page = ref(1)
const pageSize = 10
const total = ref(0)

// 详情抽屉状态
const drawerVisible = ref(false)
const detailLoading = ref(false)
const currentTicket = ref<Ticket | null>(null)

/** 状态分栏（全部 + 四个状态） */
const statusGroups = [
  { value: '', label: '全部' },
  { value: 'open', label: '待处理' },
  { value: 'in_progress', label: '处理中' },
  { value: 'resolved', label: '已解决' },
  { value: 'closed', label: '已关闭' },
]

/** 看板分栏数量（基于当前页已加载） */
function countBy(status: string): number {
  return status ? tickets.value.filter((t) => t.status === status).length : tickets.value.length
}

/** 工单状态 -> el-tag 配色 */
function statusType(status: string): string {
  return { open: 'warning', in_progress: 'primary', resolved: 'success', closed: 'info' }[status] || 'info'
}

/** 优先级 -> el-tag 配色 */
function priorityType(priority: string): string {
  return { high: 'danger', urgent: 'danger', medium: 'warning', low: 'info' }[priority] || 'info'
}

/** ISO 时间串 -> 中文本地化时间 */
function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN')
}

/** 拉取工单列表（带状态筛选 + 分页），失败提示 */
async function loadTickets() {
  loading.value = true
  try {
    const res = await getTicketList({ page: page.value, page_size: pageSize, status: activeStatus.value || undefined })
    tickets.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

/** 打开详情抽屉并拉取工单完整详情 */
async function showDetail(row: Ticket) {
  drawerVisible.value = true
  currentTicket.value = null
  detailLoading.value = true
  try {
    const res = await getTicketDetail(row.id)
    currentTicket.value = res.data
  } catch (e: any) {
    // 详情接口失败时退回展示列表行已有数据(仍比 ElMessage.info 好)
    currentTicket.value = row
    ElMessage.warning('完整详情加载失败,展示概要信息')
  } finally {
    detailLoading.value = false
  }
}

onMounted(loadTickets)
</script>

<style scoped lang="scss">
.detail-body { padding: 0 4px; }
.detail-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--zh-text-primary);
  margin: 0 0 var(--zh-gap);
  line-height: 1.5;
}
.desc-section { margin-top: var(--zh-gap-lg); }
.desc-label { margin-bottom: var(--zh-gap-sm); }
.desc-content {
  color: var(--zh-text-regular);
  line-height: 1.7;
  background: var(--zh-bg-page);
  padding: var(--zh-gap);
  border-radius: var(--zh-radius-sm);
  white-space: pre-wrap;
}
</style>
