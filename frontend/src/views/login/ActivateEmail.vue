<!--
  ActivateEmail.vue - 公开邮箱激活落点，展示处理中、成功、过期与失败状态。
-->
<template>
  <main class="public-result-page">
    <el-card class="result-card" shadow="never">
      <el-result
        v-if="state === 'loading'"
        icon="info"
        title="正在激活邮箱"
        sub-title="请稍候，正在验证激活链接。"
      />
      <el-result
        v-else-if="state === 'success'"
        icon="success"
        title="激活成功"
        sub-title="邮箱已验证，现在可以登录 ZhiHub。"
      >
        <template #extra><el-button type="primary" @click="router.push('/login')">前往登录</el-button></template>
      </el-result>
      <el-result
        v-else-if="state === 'expired'"
        icon="warning"
        title="激活链接已过期"
        sub-title="请返回登录页重新发送激活邮件。"
      >
        <template #extra><el-button @click="router.push('/login')">返回登录</el-button></template>
      </el-result>
      <el-result v-else icon="error" title="激活失败" :sub-title="errorMessage">
        <template #extra><el-button @click="router.push('/login')">返回登录</el-button></template>
      </el-result>
    </el-card>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { activateEmail } from '@/api/auth'

type State = 'loading' | 'success' | 'expired' | 'error'
const route = useRoute()
const router = useRouter()
const state = ref<State>('loading')
const errorMessage = ref('激活链接无效，请确认链接完整后重试。')

onMounted(async () => {
  const token = typeof route.query.token === 'string' ? route.query.token : ''
  if (!token) {
    state.value = 'error'
    return
  }
  try {
    await activateEmail(token)
    state.value = 'success'
  } catch (error: any) {
    const message = error?.message || error?.detail || '激活失败，请稍后重试。'
    errorMessage.value = message
    state.value = error?.status === 400 && /过期|无效/.test(message) ? 'expired' : 'error'
  }
})
</script>

<style scoped>
.public-result-page { min-height: 100vh; display: grid; place-items: center; background: var(--zh-bg-page, #f5f7fa); padding: 24px; }
.result-card { width: min(560px, 100%); }
</style>
