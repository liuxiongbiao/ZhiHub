/**
 * src/components/chat/ChatWindow.vue
 *
 * 职责: 会话聊天窗口 —— 支持管理员/坐席/终端用户三种模式（mode prop）；
 *      消息发送（含 SSE 流式）、👍/👎 反馈、CSAT 与留言弹窗调度、WebSocket 实时消息与输入状态展示。
 *
 * 依赖: session API（发送/流式/反馈）、WsClient（实时事件）、CsatDialog、LeaveMessageDialog。
 */
<template>
  <div class="chat-window">
    <div class="messages-area" ref="messagesRef">
      <el-alert
        v-if="sessionStatus === 'closed' && sessionExpired"
        type="warning"
        :closable="false"
        show-icon
        class="closed-alert"
      >
        <template #title>
          <span>会话已关闭超过 24 小时</span>
          <el-button link type="primary" size="small" @click="emit('new-session')">开始新会话</el-button>
        </template>
      </el-alert>
      <el-alert
        v-if="mode === 'user' && sessionStatus === 'human_handling'"
        title="人工客服已接管，请等待客服回复"
        type="info"
        :closable="false"
        show-icon
        class="closed-alert"
      />
      <div v-if="messages.length === 0" class="empty-hint">发送一条消息开始对话</div>
      <div v-for="msg in messages" :key="msg.message_id || msg._localId">
        <MessageBubble
          :role="msg.role"
          :content="msg.content"
          :sources="msg.sources"
          :created-at="msg.created_at"
        />
        <FeedbackButtons
          v-if="msg.role === 'assistant' && msg.message_id"
          :message-id="msg.message_id"
          @feedback="handleFeedback"
        />
      </div>
      <div v-if="loading" class="typing-indicator"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
    </div>
    <div class="input-area">
      <el-input v-model="input" :placeholder="inputPlaceholder" :disabled="loading || inputDisabled" @keyup.enter="handleSend" clearable>
        <template #append>
          <el-button type="primary" :loading="loading" :disabled="!input.trim() || inputDisabled" @click="handleSend">发送</el-button>
        </template>
      </el-input>
    </div>
    <!-- 规格 013 US4: CSAT 评价（closed 后弹出） -->
    <CsatDialog v-model="showCsat" :session-id="sessionId" />
    <!-- 规格 013 US4: 无坐席留言（waiting_human 时） -->
    <LeaveMessageDialog v-model="showLeaveMsg" :session-id="sessionId" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getSessionDetail,
  sendAgentMessage,
  sendMessage as sendMessageApi,
  sendMessageStream,
  StreamRequestError,
  submitFeedback,
} from '@/api/session'
import { getAuthToken } from '@/api/auth-session'
import { API_BASE_URL } from '@/constants'
import { WsClient } from '@/utils/ws-client'
import MessageBubble from './MessageBubble.vue'
import FeedbackButtons from './FeedbackButtons.vue'
import CsatDialog from './CsatDialog.vue'
import LeaveMessageDialog from './LeaveMessageDialog.vue'
import type { Message, SourceItem } from '@/types/session'

interface LocalMessage { _localId?: string; message_id?: number; role: string; content: string; sources?: SourceItem[]; created_at?: string }

const props = withDefaults(defineProps<{
  sessionId: number | null
  prefillText?: string
  mode?: 'user' | 'agent'
}>(), { mode: 'user' })
const emit = defineEmits<{ (e: 'message-sent'): void; (e: 'status-change', status: string): void; (e: 'new-session'): void }>()

const messages = ref<LocalMessage[]>([])
const input = ref('')
const loading = ref(false)
const messagesRef = ref<HTMLElement | null>(null)
const sessionStatus = ref('')
const sessionUpdatedAt = ref('')
const sessionExpired = ref(false)
const inputDisabled = ref(false)
const showCsat = ref(false)
const showLeaveMsg = ref(false)
let sessionWs: WsClient | null = null
const inputPlaceholder = computed(() => {
  if (props.mode === 'user' && sessionStatus.value === 'human_handling') return '人工客服处理中'
  return props.mode === 'agent' ? '输入客服回复...' : '输入您的问题...'
})

function scrollToBottom() { nextTick(() => { if (messagesRef.value) messagesRef.value.scrollTop = messagesRef.value.scrollHeight }) }

function loadHistory(historyMessages: Message[]) {
  messages.value = historyMessages.map(m => ({ message_id: m.message_id, role: m.role, content: m.content, sources: m.sources, created_at: m.created_at }))
  scrollToBottom()
}

onMounted(async () => {
  if (!props.sessionId) return
  try {
    const res = await getSessionDetail(props.sessionId)
    const detail = res.data
    if (detail?.messages?.length) loadHistory(detail.messages as unknown as Message[])
    if (detail?.updated_at) sessionUpdatedAt.value = detail.updated_at
    if (detail?.status) updateStatus(detail.status)
  } catch { /* 历史加载失败不阻塞聊天 */ }
  if (props.mode === 'user') connectSessionWs()
})

function connectSessionWs() {
  if (!props.sessionId) return
  const token = getAuthToken()
  if (!token) return
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${protocol}//${window.location.host}${API_BASE_URL}/ws/session/${props.sessionId}?token=${encodeURIComponent(token)}`
  sessionWs = new WsClient(url)
  sessionWs.onMessage((message) => {
    if (message.type !== 'agent_message') return
    const payload = message.payload
    if (Number(payload.session_id) !== props.sessionId) return
    messages.value.push({
      message_id: Number(payload.message_id),
      role: 'agent',
      content: String(payload.content ?? ''),
      created_at: new Date().toISOString(),
    })
    scrollToBottom()
  })
  sessionWs.connect().catch(() => {})
}

onUnmounted(() => {
  sessionWs?.close()
  sessionWs = null
})

function updateStatus(status: string) {
  sessionStatus.value = status
  // PRD §3.8：CLOSED 后 24h 内可续问（保留上下文）；超 24h 提示新建会话
  sessionExpired.value = status === 'closed' && !!sessionUpdatedAt.value &&
    (Date.now() - new Date(sessionUpdatedAt.value).getTime()) > 24 * 60 * 60 * 1000
  inputDisabled.value = (status === 'closed' && sessionExpired.value) ||
    (props.mode === 'user' && status === 'human_handling')
  emit('status-change', status)
  // 规格 013 US4: closed → 弹 CSAT；waiting_human → 弹留言
  if (status === 'closed') showCsat.value = true
  if (status === 'waiting_human') showLeaveMsg.value = true
}

// 规格 013 US5: 快捷回复预填（prefillText 变化时填入输入框）
watch(() => props.prefillText, (v) => { if (v) input.value = v })

async function handleSend() {
  const text = input.value.trim()
  if (!text || !props.sessionId || loading.value) return
  const senderRole = props.mode === 'agent' ? 'agent' : 'user'
  messages.value.push({ _localId: `${senderRole}-${Date.now()}`, role: senderRole, content: text, created_at: new Date().toISOString() })
  input.value = ''
  scrollToBottom()
  loading.value = true
  try {
    if (props.mode === 'agent') {
      const response = await sendAgentMessage(props.sessionId, text)
      const local = messages.value[messages.value.length - 1]
      messages.value[messages.value.length - 1] = {
        ...local,
        message_id: response.data.message_id,
      }
      emit('message-sent')
      return
    }
    // 规格 013 US7: 优先走 SSE 流式（后端已复用 Agent 主链路），逐字渲染
    const assistantIdx = messages.value.length
    messages.value.push({ _localId: `assistant-${Date.now()}`, role: 'assistant', content: '', created_at: new Date().toISOString() })
    scrollToBottom()
    try {
      let streamedMessageId: number | undefined
      await sendMessageStream(props.sessionId, text, (chunk) => {
        // 规格 013: 直接修改原始对象不会触发 Vue 响应式（push 后数组存的是代理），
        // 必须替换数组元素才会重渲染 —— 否则流式气泡 content 永远为空（只显示角色名）。
        const current = messages.value[assistantIdx]
        messages.value[assistantIdx] = { ...current, content: current.content + chunk }
        scrollToBottom()
      }, (messageId) => {
        streamedMessageId = messageId
      })
      // 规格 013: SSE 结束前后端下发落库的 assistant message_id，关联本地气泡（刷新后可对应反馈等操作）
      if (streamedMessageId) {
        const current = messages.value[assistantIdx]
        messages.value[assistantIdx] = { ...current, message_id: streamedMessageId }
      }
    } catch (streamError) {
      // 鉴权、CSRF 和业务错误必须直接展示，禁止再发一次非流式请求。
      if (streamError instanceof StreamRequestError && ![404, 405, 501].includes(streamError.status)) {
        throw streamError
      }
      // 流式端点不可用时回退非流式 /message（后端仍支持）
      messages.value.pop()
      const res = await sendMessageApi(props.sessionId, text)
      messages.value.push({ message_id: res.data.message_id, role: res.data.role, content: res.data.content, sources: res.data.sources })
    }
    emit('message-sent')
  } catch (e: any) {
    ElMessage.error(e?.message || '发送失败')
    messages.value.push({ _localId: `error-${Date.now()}`, role: 'system', content: '消息发送失败，请重试。' })
  } finally { loading.value = false; scrollToBottom() }
}

/** 处理回复反馈（PRD §§3.8） */
async function handleFeedback(data: { messageId: number; type: string }) {
  try {
    const score = data.type === 'like' ? 1 : -1
    await submitFeedback(data.messageId, score)
    ElMessage.success(data.type === 'like' ? '感谢反馈' : '已标记为无用回复')
  } catch (e: any) {
    ElMessage.error(e?.message || '反馈提交失败')
  }
}

defineExpose({ loadHistory })
</script>

<style scoped>
.chat-window { display: flex; flex-direction: column; height: 100%; border: 1px solid #e4e7ed; border-radius: 12px; overflow: hidden; background: #fff; }
.messages-area { flex: 1; overflow-y: auto; padding: 16px; }
.closed-alert { margin-bottom: 12px; }
.empty-hint { text-align: center; color: #999; margin-top: 60px; }
.input-area { padding: 12px; border-top: 1px solid #e4e7ed; }
.typing-indicator { display: flex; gap: 4px; padding: 8px; }
.typing-indicator .dot { width: 8px; height: 8px; border-radius: 50%; background: #c0c4cc; animation: blink 1.4s infinite both; }
.typing-indicator .dot:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator .dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink { 0%, 80%, 100% { opacity: 0.3; } 40% { opacity: 1; } }
</style>
