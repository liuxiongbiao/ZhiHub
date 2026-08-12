<!-- frontend/src/components/chat/FeedbackButtons.vue -->
<!-- 职责: 👍/👎反馈按钮（收集满意度+bad case标记） -->
<template>
  <div class="feedback-buttons">
    <el-button
      :type="feedback === 'like' ? 'primary' : 'default'"
      size="small"
      circle
      @click="handleFeedback('like')"
      :disabled="submitted"
    >👍</el-button>
    <el-button
      :type="feedback === 'dislike' ? 'danger' : 'default'"
      size="small"
      circle
      @click="handleFeedback('dislike')"
      :disabled="submitted"
    >👎</el-button>
    <span v-if="submitted" class="thanks-text">感谢反馈</span>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ messageId: number }>()
const emit = defineEmits<{ (e: 'feedback', data: { messageId: number; type: string }): void }>()

const feedback = ref<string | null>(null)
const submitted = ref(false)

/** 点击反馈按钮: 记录类型并通知父组件 */
function handleFeedback(type: string) {
  feedback.value = type
  submitted.value = true
  emit('feedback', { messageId: props.messageId, type })
}
</script>

<style scoped>
.feedback-buttons { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
.thanks-text { font-size: 12px; color: #999; margin-left: 4px; }
</style>
