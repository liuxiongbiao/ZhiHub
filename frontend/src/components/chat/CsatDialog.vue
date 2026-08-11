<template>
  <el-dialog v-model="visible" title="满意度评价" width="400px" :close-on-click-modal="false">
    <div class="csat-body">
      <p>请为本次服务评分：</p>
      <el-rate v-model="score" :max="5" show-text :texts="['很不满意','不满意','一般','满意','很满意']" />
      <el-input v-model="comment" type="textarea" :rows="3" placeholder="可选：留下您的建议" class="csat-comment" />
    </div>
    <template #footer>
      <el-button @click="visible = false">跳过</el-button>
      <el-button type="primary" :loading="loading" @click="submit">提交评价</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { submitCsat } from '@/api/session'

const props = defineProps<{ modelValue: boolean; sessionId: number | null }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; done: [] }>()

const visible = ref(props.modelValue)
const score = ref(5)
const comment = ref('')
const loading = ref(false)
watch(() => props.modelValue, (v) => { visible.value = v })
watch(visible, (v) => emit('update:modelValue', v))

async function submit() {
  if (!props.sessionId) return
  loading.value = true
  try {
    await submitCsat(props.sessionId, score.value, comment.value || undefined)
    ElMessage.success('感谢您的评价')
    visible.value = false
    emit('done')
  } catch (e: any) {
    if (e?.status === 409) ElMessage.warning('您已评价过本次会话')
    else ElMessage.error(e?.message || '提交失败')
  } finally { loading.value = false }
}
</script>
<style scoped>
.csat-body { text-align: center; }
.csat-comment { margin-top: 16px; text-align: left; }
</style>
