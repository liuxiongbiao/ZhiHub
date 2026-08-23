/**
 * src/components/chat/LeaveMessageDialog.vue
 *
 * 职责: 转人工留言弹窗 —— 当前无在线坐席时收集用户诉求，
 *      提交 POST /session/{id}/leave-message 由服务端自动创建留言工单。
 *
 * 交互: ChatWindow 在会话进入 waiting_human 且无坐席在线时通过 v-model 唤起。
 */
<template>
  <el-dialog v-model="visible" title="留言" width="440px" :close-on-click-modal="false">
    <p class="leave-tip">当前无在线坐席，请留下您的诉求，我们将尽快处理。</p>
    <el-input v-model="content" type="textarea" :rows="4" placeholder="请输入留言内容" />
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">提交留言</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { leaveMessage } from '@/api/session'

const props = defineProps<{ modelValue: boolean; sessionId: number | null }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; done: [] }>()

const visible = ref(props.modelValue)
const content = ref('')
const loading = ref(false)
watch(() => props.modelValue, (v) => { visible.value = v })
watch(visible, (v) => emit('update:modelValue', v))

async function submit() {
  if (!props.sessionId) return
  if (!content.value.trim()) { ElMessage.warning('请输入留言内容'); return }
  loading.value = true
  try {
    const res = await leaveMessage(props.sessionId, content.value.trim())
    ElMessage.success(`留言已记录，工单号：${res.data.ticket_no}`)
    visible.value = false
    emit('done')
  } catch (e: any) {
    if (e?.status === 409) ElMessage.warning('当前已有在线坐席，请等待认领')
    else ElMessage.error(e?.message || '提交失败')
  } finally { loading.value = false }
}
</script>
<style scoped>
.leave-tip { color: #e6a23c; font-size: 13px; margin-bottom: 12px; }
</style>
