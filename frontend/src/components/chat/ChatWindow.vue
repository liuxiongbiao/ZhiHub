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
      <div v-if="messages.length === 0" class="empty-hint">发送一条消息开始对话</div>
      <MessageBubble
        v-for="msg in messages"
        :key="msg.message_id || msg._localId"
        :role="msg.role"
        :content="msg.content"
        :sources="msg.sources"
        :created-at="msg.created_at"
      />
      <div v-if="loading" class="typing-indicator"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
    </div>
    <div class="input-area">
      <el-input v-model="input" placeholder="输入您的问题..." :disabled="loading || inputDisabled" @keyup.enter="handleSend" clearable>
        <template #append>
          <el-button type="primary" :loading="loading" :disabled="!input.trim() || inputDisabled" @click="handleSend">发送</el-button>
        </template>
      </el-input>
    </div>
    <!-- 013 US4: CSAT 评价（closed 后弹出） -->
    <CsatDialog v-model="showCsat" :session-id="sessionId" />
    <!-- 013 US4: 无坐席留言（waiting_human 时） -->
    <LeaveMessageDialog v-model="showLeaveMsg" :session-id="sessionId" />
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getSessionDetail, sendMessage as sendMessageApi, sendMessageStream } from '@/api/session'
import MessageBubble from './MessageBubble.vue'
import CsatDialog from './CsatDialog.vue'
import LeaveMessageDialog from './LeaveMessageDialog.vue'
import type { Message, SourceItem } from '@/types/session'

interface LocalMessage { _localId?: string; message_id?: number; role: string; content: string; sources?: SourceItem[]; created_at?: string }

const props = defineProps<{
  sessionId: number | null
  prefillText?: string
}>()
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
})

function updateStatus(status: string) {
  sessionStatus.value = status
  // PRD 3.8：CLOSED 后 24h 内可续问（保留上下文）；超 24h 提示新建会话
  sessionExpired.value = status === 'closed' && !!sessionUpdatedAt.value &&
    (Date.now() - new Date(sessionUpdatedAt.value).getTime()) > 24 * 60 * 60 * 1000
  inputDisabled.value = loading.value || (status === 'closed' && sessionExpired.value)
  emit('status-change', status)
  // 013 US4: closed -> 弹 CSAT；waiting_human -> 弹留言
  if (status === 'closed') showCsat.value = true
  if (status === 'waiting_human') showLeaveMsg.value = true
}

// 013 US5: 快捷回复预填（prefillText 变化时填入输入框）
watch(() => props.prefillText, (v) => { if (v) input.value = v })

async function handleSend() {
  const text = input.value.trim()
  if (!text || !props.sessionId || loading.value) return
    messages.value.push({ _localId: `user-${Date.now()}`, role: 'user', content: text, created_at: new Date().toISOString() })
  input.value = ''
  scrollToBottom()
  loading.value = true
  try {
    // 013 US7: 优先走 SSE 流式（后端已复用 Agent 主链路），逐字渲染
    const assistantIdx = messages.value.length
    messages.value.push({ _localId: `assistant-${Date.now()}`, role: 'assistant', content: '', created_at: new Date().toISOString() })
    scrollToBottom()
    try {
      let streamedMessageId: number | undefined
      await sendMessageStream(props.sessionId, text, (chunk) => {
        // 013: 直接修改原始对象不会触发 Vue 响应式（push 后数组存的是代理），
        // 必须替换数组元素才会重渲染 —— 否则流式气泡 content 永远为空（只显示角色名）。
        const current = messages.value[assistantIdx]
        messages.value[assistantIdx] = { ...current, content: current.content + chunk }
        scrollToBottom()
      }, (messageId) => {
        streamedMessageId = messageId
      })
      // 013: SSE 结束前后端下发落库的 assistant message_id，关联本地气泡（刷新后可对应反馈等操作）
      if (streamedMessageId) {
        const current = messages.value[assistantIdx]
        messages.value[assistantIdx] = { ...current, message_id: streamedMessageId }
      }
    } catch {
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
