<template>
  <div class="agent-console">
    <h2>坐席工作台</h2>
    <div class="hint">等待人工的会话队列 —— 点击「认领」接手处理</div>
    <div class="console-body">
      <div class="session-panel">
        <el-table :data="waitingSessions" v-loading="loading" highlight-current-row @current-change="selectSession">
          <el-table-column prop="session_id" label="会话ID" width="80" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }"><el-tag type="warning" size="small">{{ STATUS_MAP[row.status] || row.status }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="intent" label="意图" width="140" show-overflow-tooltip />
          <el-table-column label="输入中" width="90">
            <template #default="{ row }">
              <el-tag v-if="typingMap[row.session_id]" type="success" size="small">输入中</el-tag>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150">
            <template #default="{ row }">
              <el-button v-if="row.status === 'waiting_human'" link type="primary" size="small" @click.stop="handleClaim(row)">认领</el-button>
              <el-button v-if="row.status === 'human_handling'" link type="warning" size="small" @click.stop="handleReturn(row)">退回AI</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div class="chat-panel">
        <div v-if="!selectedSession" class="empty-chat">从左侧选择会话开始处理</div>
        <div v-else class="chat-trace-wrap">
          <div class="chat-area">
            <ChatWindow :key="selectedSession.session_id" :session-id="selectedSession.session_id" :prefill-text="prefillText" />
            <!-- 013 US5: 快捷回复 -->
            <div class="quick-reply-bar">
              <el-popover placement="top-start" :width="320" trigger="click">
                <template #reference><el-button size="small">快捷回复</el-button></template>
                <div v-loading="qrLoading" class="qr-list">
                  <div v-if="quickReplies.length === 0" class="qr-empty">暂无模板</div>
                  <el-button v-for="qr in quickReplies" :key="qr.id" text size="small" class="qr-item" @click="useQuickReply(qr.content)">{{ qr.content }}</el-button>
                </div>
              </el-popover>
            </div>
          </div>
          <!-- 013 US3: Agent 执行轨迹 -->
          <div class="trace-area">
            <h4>Agent 执行轨迹</h4>
            <TraceTimeline :trace="currentTrace" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getSessionList, claimSession, returnToAgent, getTrace, getQuickReplies } from '@/api/session'
import type { Session } from '@/types/session'
import type { AgentTrace } from '@/types/trace'
import ChatWindow from '@/components/chat/ChatWindow.vue'
import TraceTimeline from '@/components/trace/TraceTimeline.vue'
import { WsClient } from '@/utils/ws-client'
import { AUTH_STORAGE_KEY, API_BASE_URL } from '@/constants'

const STATUS_MAP: Record<string, string> = {
  created: '已创建', routing: '路由中', agent_handling: 'AI处理', waiting_human: '等待人工',
  human_handling: '人工处理', resolved: '已解决', closed: '已关闭',
}

const waitingSessions = ref<Session[]>([])
const selectedSession = ref<Session | null>(null)
const loading = ref(false)
const currentTrace = ref<AgentTrace | null>(null)
const prefillText = ref('')
const quickReplies = ref<{ id: number; content: string; shortcut?: string | null }[]>([])
const qrLoading = ref(false)
let wsClient: WsClient | null = null
const typingMap = ref<Record<number, boolean>>({})

/** 建立坐席工作台 WebSocket（013 US8，替换 10s 轮询） */
function connectWs() {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    const token = raw ? (JSON.parse(raw).token ?? '') : ''
    if (!token) return
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${proto}//${window.location.host}${API_BASE_URL}/ws/agent?token=${encodeURIComponent(token)}`
    wsClient = new WsClient(wsUrl)
    wsClient.onMessage((msg) => {
      if (msg.type === 'new_session') {
        // 新待认领会话实时刷新队列
        loadWaiting()
      } else if (msg.type === 'typing' && typeof msg.session_id === 'number') {
        typingMap.value[msg.session_id] = Boolean(msg.is_typing)
      }
    })
    wsClient.connect()
  } catch {
    // WS 连接失败时保持初始加载，不阻塞工作台
  }
}

async function loadWaiting() {
  loading.value = true
  try {
    const res = await getSessionList({ page_size: 50 })
    waitingSessions.value = res.data.items.filter((s) => s.status === 'waiting_human' || s.status === 'human_handling')
  } catch { /* 静默失败 */ } finally { loading.value = false }
}

function selectSession(row: Session | null) {
  selectedSession.value = row
  prefillText.value = ''
  if (row) loadTrace(row.session_id)
}

async function loadTrace(sessionId: number) {
  try {
    const res = await getTrace(sessionId)
    const traces = res.data.traces
    currentTrace.value = traces && traces.length > 0 ? traces[traces.length - 1] : null
  } catch { currentTrace.value = null }
}

async function handleClaim(row: Session) {
  try {
    await claimSession(row.session_id)
    ElMessage.success(`已认领会话 #${row.session_id}`)
    await loadWaiting()
    selectedSession.value = row
    loadTrace(row.session_id)
  } catch (e: any) {
    ElMessage.error(e?.message || '认领失败（可能已被他人认领）')
    loadWaiting()
  }
}

async function handleReturn(row: Session) {
  try {
    await returnToAgent(row.session_id)
    ElMessage.success(`已退回给 AI #${row.session_id}`)
    selectedSession.value = null
    await loadWaiting()
  } catch (e: any) {
    ElMessage.error(e?.message || '退回失败')
  }
}

async function loadQuickReplies() {
  qrLoading.value = true
  try {
    const res = await getQuickReplies()
    quickReplies.value = res.data || []
  } catch { /* 静默 */ } finally { qrLoading.value = false }
}

function useQuickReply(content: string) {
  prefillText.value = ''
  // 触发 watch：先清空再设值
  setTimeout(() => { prefillText.value = content }, 0)
}

onMounted(() => {
  loadWaiting()
  loadQuickReplies()
  connectWs()
})
onUnmounted(() => {
  wsClient?.close()
  wsClient = null
})
</script>

<style scoped>
.agent-console { padding: 20px; height: calc(100vh - 100px); display: flex; flex-direction: column; }
.hint { color: #999; font-size: 13px; margin-bottom: 12px; }
.console-body { display: flex; gap: 16px; flex: 1; min-height: 0; }
.session-panel { width: 420px; flex-shrink: 0; overflow-y: auto; }
.chat-panel { flex: 1; display: flex; }
.empty-chat { margin: auto; color: #999; }
.chat-trace-wrap { flex: 1; display: flex; flex-direction: column; gap: 12px; width: 100%; }
.chat-area { flex: 1; display: flex; flex-direction: column; border: 1px solid #e4e7ed; border-radius: 8px; overflow: hidden; }
.chat-area :deep(.chat-window) { flex: 1; border: none; border-radius: 0; }
.quick-reply-bar { padding: 8px; border-top: 1px solid #e4e7ed; }
.trace-area { height: 220px; overflow-y: auto; border: 1px solid #e4e7ed; border-radius: 8px; padding: 12px; }
.trace-area h4 { margin: 0 0 8px; font-size: 14px; }
.qr-list { max-height: 240px; overflow-y: auto; }
.qr-item { display: block; text-align: left; width: 100%; margin-bottom: 4px; }
.qr-empty { color: #999; text-align: center; padding: 12px; }
</style>
