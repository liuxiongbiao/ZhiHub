<!--
  AcceptInvite.vue - 公开邀请落点；匿名用户登录后带回，登录用户确认加入。
-->
<template>
  <main class="public-result-page">
    <el-card class="result-card" shadow="never">
      <el-result v-if="!token" icon="error" title="邀请链接无效" sub-title="链接缺少邀请 token，请联系管理员重新邀请。" />
      <el-result v-else-if="state === 'expired'" icon="warning" title="邀请已失效" sub-title="邀请链接可能已过期或被使用，请联系管理员重新邀请。">
        <template #extra><el-button @click="router.push('/login')">返回登录</el-button></template>
      </el-result>
      <el-result v-else-if="state === 'success'" icon="success" title="加入成功" sub-title="已切换到受邀租户。">
        <template #extra><el-button type="primary" @click="router.push('/agent/console')">进入客服工作台</el-button></template>
      </el-result>
      <el-result v-else-if="!userStore.isLoggedIn" icon="info" title="登录后接受邀请" sub-title="登录成功后会自动带回当前邀请页面。">
        <template #extra>
          <el-button data-test="login-first" type="primary" @click="goLogin">前往登录</el-button>
        </template>
      </el-result>
      <el-result v-else-if="state === 'error'" icon="error" title="加入失败" :sub-title="errorMessage" />
      <el-result v-else icon="info" title="确认加入团队" :sub-title="`将以 ${userStore.phone || '当前账号'} 加入该租户。`">
        <template #extra>
          <el-button data-test="accept-invite" type="primary" :loading="state === 'loading'" @click="confirmAccept">确认加入</el-button>
        </template>
      </el-result>
    </el-card>
  </main>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { acceptInvite } from '@/api/tenant'
import { useUserStore } from '@/store/modules/user'

type State = 'idle' | 'loading' | 'success' | 'expired' | 'error'
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const state = ref<State>('idle')
const errorMessage = ref('暂时无法接受邀请，请稍后重试。')
const token = computed(() => typeof route.query.token === 'string' ? route.query.token : '')

function goLogin() {
  router.push({ path: '/login', query: { redirect: route.fullPath } })
}

async function confirmAccept() {
  if (!token.value || state.value === 'loading') return
  state.value = 'loading'
  try {
    const { data } = await acceptInvite({ token: token.value })
    userStore.$patch({
      token: data.access_token,
      refresh_token: data.refresh_token,
      user_id: data.user_id,
      tenant_id: data.tenant_id,
      role: data.role,
    })
    userStore.persist()
    state.value = 'success'
  } catch (error: any) {
    errorMessage.value = error?.message || error?.detail || errorMessage.value
    state.value = error?.status === 410 ? 'expired' : 'error'
  }
}
</script>

<style scoped>
.public-result-page { min-height: 100vh; display: grid; place-items: center; background: var(--zh-bg-page, #f5f7fa); padding: 24px; }
.result-card { width: min(560px, 100%); }
</style>
