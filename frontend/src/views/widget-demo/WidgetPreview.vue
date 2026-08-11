<!-- frontend/src/views/widget-demo/WidgetPreview.vue -->
<!-- 职责: 终端用户对话Widget（demo简化为独立页面，非真实嵌入script） -->
<!-- 分工: 🟡 交互细节（打字状态/过渡提示）弱模型起草，体验由强模型把关 -->
<!-- 对应日程: Day 4 -->
<template>
  <div class="widget-preview">
    <div class="widget-header">
      <h3>🤖 智枢 ZhiHub 智能助手</h3>
      <el-tag v-if="sessionId" type="success" size="small">已连接</el-tag>
      <el-tag v-else type="info" size="small">未连接</el-tag>
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
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createSession } from '@/api/session'
import ChatWindow from '@/components/chat/ChatWindow.vue'

const sessionId = ref<number | null>(null)
const creating = ref(false)
const chatRef = ref<InstanceType<typeof ChatWindow> | null>(null)

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
