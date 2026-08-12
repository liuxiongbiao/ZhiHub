<!-- frontend/src/views/widget-demo/WidgetPreview.vue -->
<!-- 职责: 终端用户对话Widget（demo简化为独立页面，非真实嵌入script） -->
<template>
  <div class="widget-preview">
    <div class="widget-header">
      <h3>🤖 智枢 ZhiHub 智能助手</h3>
      <el-tag v-if="sessionId" type="success" size="small">已连接</el-tag>
      <el-tag v-else type="info" size="small">未连接</el-tag>
      <el-button link type="primary" size="small" @click="openTicket">提交工单</el-button>
    </div>

    <!-- 会话创建 -->
    <div v-if="!sessionId" class="start-area">
      <p>点击下方按钮开始与智能助手对话</p>
      <el-button type="primary" :loading="creating" @click="handleCreate">
        开始对话
      </el-button>
    </div>

    <!-- 聊天窗口 -->
    <div v-else class="chat-area">
      <ChatWindow
        ref="chatRef"
        :session-id="sessionId"
        @message-sent="onMessageSent"
        @new-session="handleCreate"
      />
    </div>
  </div>

    <!-- closure-plan P1-4: 用户提交工单弹窗 -->
    <el-dialog v-model="ticketDialog" title="提交工单" width="460px">
      <el-form :model="ticketForm" label-width="70px">
        <el-form-item label="标题">
          <el-input v-model="ticketForm.title" placeholder="请简述问题" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="ticketForm.description" type="textarea" :rows="4" placeholder="详细描述您遇到的问题" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="ticketForm.priority" style="width: 140px">
            <el-option label="低" value="low" />
            <el-option label="中" value="medium" />
            <el-option label="高" value="high" />
            <el-option label="紧急" value="urgent" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ticketDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitTicket">提交</el-button>
      </template>
    </el-dialog>

</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createSession } from '@/api/session'
import { createTicket } from '@/api/ticket'
import ChatWindow from '@/components/chat/ChatWindow.vue'

const sessionId = ref<number | null>(null)
const creating = ref(false)
const chatRef = ref<InstanceType<typeof ChatWindow> | null>(null)
const ticketDialog = ref(false)
const submitting = ref(false)
const ticketForm = ref({ title: '', description: '', priority: 'medium' })

/** 创建会话，成功后展示聊天窗口 */
async function handleCreate() {
  creating.value = true
  try {
    const res = await createSession()
    sessionId.value = res.data.session_id
    ElMessage.success('会话已创建')
  } catch (e: any) {
    ElMessage.error(e?.message || '创建会话失败')
  } finally {
    creating.value = false
  }
}

/** 消息发送回调（预留埋点/追踪入口） */
function onMessageSent() {
  // 可用于后续埋点/追踪
}

function openTicket() {
  ticketForm.value = { title: '', description: '', priority: 'medium' }
  ticketDialog.value = true
}

async function submitTicket() {
  if (!ticketForm.value.title.trim()) { ElMessage.warning('请输入工单标题'); return }
  submitting.value = true
  try {
    const res = await createTicket({
      title: ticketForm.value.title.trim(),
      description: ticketForm.value.description.trim() || undefined,
      priority: ticketForm.value.priority,
      category: 'user',
    })
    ElMessage.success(`工单已提交：${res.data.ticket_no}`)
    ticketDialog.value = false
  } catch (e: any) {
    ElMessage.error(e?.message || '提交失败')
  } finally { submitting.value = false }
}

</script>

<style scoped>
.widget-preview {
  max-width: 700px;
  margin: 20px auto;
  height: 85vh;
  display: flex;
  flex-direction: column;
}

.widget-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  border-radius: 12px 12px 0 0;
}

.widget-header h3 {
  margin: 0;
  font-size: 16px;
}

.start-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-top: none;
  border-radius: 0 0 12px 12px;
  gap: 16px;
  color: #666;
}

.chat-area {
  flex: 1;
  border: 1px solid #e4e7ed;
  border-top: none;
  border-radius: 0 0 12px 12px;
  overflow: hidden;
}

.chat-area :deep(.chat-window) {
  border: none;
  border-radius: 0;
}
</style>
