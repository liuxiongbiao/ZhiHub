<!-- frontend/src/components/chat/MessageBubble.vue -->
<!-- 职责: 消息气泡 - AI/用户/坐席/系统消息样式 + 引用来源展示 -->
<template>
  <div :class="['message-bubble', role]">
    <div class="bubble-header">
      <span class="role-label">{{ roleLabel }}</span>
      <span v-if="createdAt" class="bubble-time">{{ formatTime(createdAt) }}</span>
    </div>
    <div class="bubble-content" v-html="renderedContent"></div>
    <!-- 引用来源 -->
    <div v-if="sources && sources.length > 0" class="sources-section">
      <div class="sources-title">📎 来源:</div>
      <div v-for="(src, idx) in sources" :key="idx" class="source-item">
        <span class="source-doc">{{ src.doc }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { SourceItem } from '@/types/session'

const props = defineProps<{
  role: string
  content: string
  sources?: SourceItem[]
  createdAt?: string
}>()

/** 消息时间戳（PRD 3.8：每条消息带时间） */
function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

/** 角色 -> 中文标签 */
const roleLabel = computed(() => {
  switch (props.role) {
    case 'user': return '用户'
    case 'assistant': return '智能助手'
    case 'agent': return '坐席'
    case 'system': return '系统'
    default: return props.role
  }
})

/**
 * HTML 转义：v-html 渲染前必须消毒，防存储型 XSS
 * （终端用户消息可含 <img onerror=...> 等，存库后由坐席端渲染）。
 * & 必须最先转义，否则 < 转出的 &lt; 会被二次转义成 &amp;lt;。
 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 将消息内容中的换行渲染为 <br> 标签（先 HTML 转义再替换换行，防 XSS） */
const renderedContent = computed(() => {
  return escapeHtml(props.content).replace(/\n/g, '<br>')
})
</script>

<style scoped>
.message-bubble {
  padding: 12px 16px;
  border-radius: 10px;
  margin-bottom: 12px;
  max-width: 80%;
}
.message-bubble.user {
  background: #409eff;
  color: #fff;
  margin-left: auto;
}
.message-bubble.assistant {
  background: #f0f2f5;
  color: #333;
  margin-right: auto;
}
.message-bubble.agent {
  background: #e6f7ff;
  color: #333;
  margin-right: auto;
}
.message-bubble.system {
  background: #fff7e6;
  color: #666;
  text-align: center;
  margin: 8px auto;
  max-width: 60%;
  font-size: 13px;
}
.bubble-header {
  font-size: 12px;
  margin-bottom: 4px;
  opacity: 0.7;
}
.bubble-time {
  margin-left: 8px;
  font-size: 11px;
  color: #c0c4cc;
}
.bubble-content {
  line-height: 1.6;
  word-break: break-word;
}
.sources-section {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 0, 0, 0.08);
  font-size: 12px;
}
.sources-title {
  font-weight: 500;
  margin-bottom: 4px;
}
.source-item {
  opacity: 0.75;
}
</style>
