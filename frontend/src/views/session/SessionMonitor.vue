<!-- frontend/src/views/session/SessionMonitor.vue -->
<!-- 013 审查补齐：会话监控列表（PRD 3.8：状态/等待时长，点击进详情查看完整 trace） -->
<template>
  <div>
    <PageHeader title="会话监控" subtitle="实时查看本租户全部会话与等待人工时长" />
    <div class="page-container">
      <el-card shadow="never">
        <div class="toolbar">
          <el-radio-group v-model="statusFilter" @change="loadSessions">
            <el-radio-button label="">全部</el-radio-button>
            <el-radio-button label="agent_handling">AI处理中</el-radio-button>
            <el-radio-button label="waiting_human">等待人工</el-radio-button>
            <el-radio-button label="human_handling">人工处理</el-radio-button>
            <el-radio-button label="closed">已关闭</el-radio-button>
          </el-radio-group>
        </div>

        <el-table :data="sessions" v-loading="loading" stripe highlight-current-row @row-click="goDetail">
          <el-table-column prop="session_id" label="会话ID" width="90" />
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" size="small">{{ STATUS_MAP[row.status] || row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="等待/时长" width="140">
            <template #default="{ row }">{{ durationLabel(row) }}</template>
          </el-table-column>
          <el-table-column prop="intent" label="意图" width="150" show-overflow-tooltip />
          <el-table-column prop="sentiment" label="情绪" width="90" />
          <el-table-column prop="message_count" label="消息数" width="90" />
          <el-table-column prop="updated_at" label="最近更新" width="180">
            <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
          </el-table-column>
        </el-table>

        <div class="pagination-wrap">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="total, prev, pager, next"
            @current-change="loadSessions"
          />
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getSessionList } from '@/api/session'
import type { Session } from '@/types/session'
import PageHeader from '@/components/common/PageHeader.vue'

const STATUS_MAP: Record<string, string> = {
  created: '已创建', routing: '路由中', agent_handling: 'AI处理中',
  waiting_human: '等待人工', human_handling: '人工处理', resolved: '已解决', closed: '已关闭',
}

const router = useRouter()
const sessions = ref<Session[]>([])
const loading = ref(false)
const statusFilter = ref('')
const page = ref(1)
const pageSize = 20
const total = ref(0)

function statusType(status: string): string {
  return { waiting_human: 'warning', human_handling: 'success', closed: 'info', agent_handling: 'primary' }[status] || 'info'
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN')
}

/** 等待人工时长 / 最后活跃时长 */
function durationLabel(row: Session): string {
  const base = new Date(row.updated_at).getTime()
  const mins = Math.max(0, Math.round((Date.now() - base) / 60000))
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟`
  return `${Math.floor(mins / 60)} 小时 ${mins % 60} 分`
}

async function loadSessions() {
  loading.value = true
  try {
    const res = await getSessionList({ page: page.value, page_size: pageSize, status: statusFilter.value || undefined })
    sessions.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    ElMessage.error(e?.message || '加载会话失败')
  } finally { loading.value = false }
}

function goDetail(row: Session) {
  router.push(`/session/${row.session_id}`)
}

onMounted(loadSessions)
</script>

<style scoped lang="scss">
.toolbar { margin-bottom: 16px; }
.pagination-wrap { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>
