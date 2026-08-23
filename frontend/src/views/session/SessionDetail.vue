<!-- frontend/src/views/session/SessionDetail.vue -->
<!-- 职责: 会话详情 - 聊天记录 + Agent trace时间线 -->
<template>
  <div class="session-detail" v-loading="loading">
    <el-result
      v-if="errorMessage"
      icon="warning"
      :title="errorMessage"
      sub-title="请检查链接，或返回会话监控重新选择会话。"
    >
      <template #extra>
        <router-link to="/session/monitor">
          <el-button type="primary">返回会话监控</el-button>
        </router-link>
      </template>
    </el-result>
    <template v-if="session">
      <!-- 会话信息 -->
      <div class="session-header">
        <h3>会话 #{{ session.session_id }}</h3>
        <div class="session-meta">
          <el-tag>{{ session.status }}</el-tag>
          <el-tag v-if="session.intent" type="info">{{ session.intent }}</el-tag>
          <span class="time">{{ formatDate(session.updated_at) }}</span>
        </div>
      </div>

      <!-- 消息列表 + Trace -->
      <div class="content-area">
        <el-tabs v-model="activeTab">
          <el-tab-pane label="对话记录" name="messages">
            <div class="messages-list">
              <div v-for="msg in session.messages" :key="msg.message_id" class="msg-item">
                <div :class="['msg-bubble', msg.role]">
                  <div class="msg-role">{{ roleLabel(msg.role) }}</div>
                  <div class="msg-content">{{ msg.content }}</div>
                  <div v-if="msg.sources && msg.sources.length" class="msg-sources">
                    📎 来源: {{ msg.sources.map(s => s.doc).join(', ') }}
                  </div>
                  <FeedbackButtons
                    v-if="msg.role === 'assistant'"
                    :message-id="msg.message_id"
                    @feedback="handleFeedback"
                  />
                </div>
              </div>
            </div>
          </el-tab-pane>
          <el-tab-pane label="Agent 执行轨迹" name="trace">
            <TraceTimeline
              v-for="trace in traces"
              :key="trace._id"
              :trace="trace"
            />
            <el-empty v-if="traces.length === 0" description="暂无执行轨迹数据" />
          </el-tab-pane>
        </el-tabs>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'
import { getSessionDetail } from '@/api/session'
import request from '@/api/request'
import TraceTimeline from '@/components/trace/TraceTimeline.vue'
import FeedbackButtons from '@/components/chat/FeedbackButtons.vue'
import type { SessionDetail, Message } from '@/types/session'
import type { AgentTrace } from '@/types/trace'

const route = useRoute()
const session = ref<SessionDetail | null>(null)
const traces = ref<AgentTrace[]>([])
const loading = ref(false)
const activeTab = ref('messages')
const errorMessage = ref('')

/** 消息角色 → 中文标签 */
function roleLabel(role: string): string {
  const map: Record<string, string> = { user: '用户', assistant: 'AI', agent: '坐席', system: '系统' }
  return map[role] || role
}

/** ISO 时间串 → 中文本地化时间 */
function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN')
}

/** 用户反馈回调（: 对接 POST /session/message/{id}/feedback，此前为空壳 console.log） */
async function handleFeedback(data: { messageId: number; type: string }) {
  // : FeedbackButtons emit 'like'/'dislike'（非 'up'/'down'），原判断 'up' 恒 False → 反馈恒 -1
  const positive = data.type === 'like'
  try {
    await request.post(`/session/message/${data.messageId}/feedback`, {
      score: positive ? 1 : -1,
    })
    ElMessage.success(positive ? '已记录有用反馈' : '已记录无用反馈')
  } catch (e: any) {
    ElMessage.error(e?.message || '反馈提交失败')
  }
}

/** 并发拉取会话详情 + Agent 执行轨迹 */
async function fetchData() {
  const id = Number(route.params.id)
  if (!Number.isSafeInteger(id) || id <= 0) {
    errorMessage.value = '无效的会话编号'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    const [detailRes, traceRes] = await Promise.all([
      getSessionDetail(id),
      request.get(`/session/${id}/trace`).catch(() => ({ data: { traces: [] } })),
    ])
    session.value = detailRes.data
    traces.value = traceRes.data.traces || []
  } catch (e: any) {
    errorMessage.value = e?.response?.status === 404 ? '会话不存在或已被删除' : '会话详情加载失败'
    ElMessage.error(e?.message || '加载会话详情失败')
  } finally {
    loading.value = false
  }
}

onMounted(fetchData)
</script>

<style scoped>
.session-detail { padding: 20px; max-width: 1000px; }
.session-header { margin-bottom: 20px; }
.session-header h3 { margin-bottom: 8px; }
.session-meta { display: flex; align-items: center; gap: 10px; }
.time { color: #999; font-size: 13px; }
.content-area { background: #fff; border-radius: 8px; }
.messages-list { padding: 16px; }
.msg-item { margin-bottom: 16px; }
.msg-bubble { padding: 12px 16px; border-radius: 10px; max-width: 80%; }
.msg-bubble.user { background: #409eff; color: #fff; margin-left: auto; }
.msg-bubble.assistant { background: #f0f2f5; }
.msg-bubble.agent { background: #e6f7ff; }
.msg-bubble.system { background: #fff7e6; text-align: center; margin: 8px auto; font-size: 13px; }
.msg-role { font-size: 12px; opacity: 0.7; margin-bottom: 4px; }
.msg-content { line-height: 1.6; white-space: pre-wrap; }
.msg-sources { font-size: 12px; margin-top: 6px; opacity: 0.7; }
</style>
