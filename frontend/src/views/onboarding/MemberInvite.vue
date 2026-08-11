<template>
  <div>
    <PageHeader title="成员邀请" subtitle="邀请坐席加入（受套餐坐席数限制）" />
    <div class="page-container">
      <el-card shadow="never" class="invite-card">
        <el-form @submit.prevent="handleInvite">
          <el-form-item label="坐席手机号">
            <el-input v-model="phone" placeholder="请输入坐席手机号" style="width: 240px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="handleInvite">生成邀请链接</el-button>
          </el-form-item>
        </el-form>
        <el-alert v-if="inviteUrl" type="success" :closable="false" class="invite-url">
          邀请链接：<a :href="inviteUrl" target="_blank">{{ inviteUrl }}</a>
        </el-alert>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { inviteMember } from '@/api/tenant'
import PageHeader from '@/components/common/PageHeader.vue'

const phone = ref('')
const loading = ref(false)
const inviteUrl = ref('')

async function handleInvite() {
  if (!/^1[3-9]\d{9}$/.test(phone.value)) { ElMessage.warning('请输入正确的手机号'); return }
  loading.value = true
  try {
    const res = await inviteMember(phone.value)
    inviteUrl.value = res.data.invite_url || ''
    ElMessage.success('邀请链接已生成')
  } catch (e: any) {
    if (e?.status === 409) ElMessage.error('已达套餐坐席数上限，请升级套餐')
    else ElMessage.error(e?.message || '邀请失败')
  } finally { loading.value = false }
}
</script>
<style scoped>
.invite-card { max-width: 600px; }
.invite-url { margin-top: 16px; }
</style>
