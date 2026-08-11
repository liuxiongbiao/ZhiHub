<!-- frontend/src/views/profile/Profile.vue -->
<!-- 013: 个人中心（密码重置 / 邮箱绑定 / 微信绑定，PRD 3.1） -->
<template>
  <div>
    <PageHeader title="个人中心" subtitle="账号安全设置" />
    <div class="page-container">
      <el-card shadow="never" class="profile-card">
        <el-tabs v-model="activeTab">
          <el-tab-pane label="修改密码" name="pwd">
            <el-form :model="pwdForm" label-position="top" @submit.prevent="handleResetPwd">
              <el-form-item label="手机号">
                <el-input v-model="pwdForm.phone" :disabled="true" />
              </el-form-item>
              <el-form-item label="验证码">
                <div class="code-row">
                  <el-input v-model="pwdForm.code" placeholder="请输入验证码" />
                  <el-button :disabled="pwdCooldown > 0" @click="sendPwdCode">
                    {{ pwdCooldown > 0 ? `${pwdCooldown}s` : '获取验证码' }}
                  </el-button>
                </div>
              </el-form-item>
              <el-form-item label="新密码">
                <el-input v-model="pwdForm.newPassword" type="password" placeholder="至少 8 位，含字母和数字" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="loading" @click="handleResetPwd">重置密码</el-button>
              </el-form-item>
            </el-form>
          </el-tab-pane>

          <el-tab-pane label="绑定邮箱" name="email">
            <el-form :model="emailForm" label-position="top" @submit.prevent="handleBindEmail">
              <el-form-item label="邮箱">
                <el-input v-model="emailForm.email" placeholder="name@example.com" />
              </el-form-item>
              <el-form-item label="邮箱验证码">
                <div class="code-row">
                  <el-input v-model="emailForm.code" placeholder="请输入邮箱验证码" />
                  <el-button :disabled="emailCooldown > 0" @click="sendEmailCode">
                    {{ emailCooldown > 0 ? `${emailCooldown}s` : '获取验证码' }}
                  </el-button>
                </div>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="loading" @click="handleBindEmail">绑定邮箱</el-button>
              </el-form-item>
            </el-form>
          </el-tab-pane>

          <el-tab-pane label="绑定微信" name="wechat">
            <div class="wechat-pane">
              <p class="muted">绑定后可使用微信扫码登录。</p>
              <el-button type="success" :loading="wxLoading" @click="handleBindWechat">微信授权绑定</el-button>
            </div>
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { sendCode, passwordReset, bindEmail, wechatAuth, wechatBind } from '@/api/auth'
import { useUserStore } from '@/store/modules/user'
import { useRoute } from 'vue-router'
import PageHeader from '@/components/common/PageHeader.vue'

const userStore = useUserStore()
const route = useRoute()
const activeTab = ref('pwd')
const loading = ref(false)
const wxLoading = ref(false)
const pwdCooldown = ref(0)
const emailCooldown = ref(0)

const pwdForm = reactive({ phone: userStore.phone || '', code: '', newPassword: '' })
const emailForm = reactive({ email: '', code: '' })

function startCooldown(target: { value: number }) {
  target.value = 60
  const t = setInterval(() => { target.value--; if (target.value <= 0) clearInterval(t) }, 1000)
}

async function sendPwdCode() {
  if (!/^1[3-9]\d{9}$/.test(pwdForm.phone)) { ElMessage.warning('手机号不正确'); return }
  try {
    await sendCode(pwdForm.phone, 'sms')
    ElMessage.success('验证码已发送')
    startCooldown(pwdCooldown)
  } catch (e: any) { ElMessage.error(e?.message || '发送失败') }
}

async function handleResetPwd() {
  if (!pwdForm.code || !pwdForm.newPassword) { ElMessage.warning('请填写验证码和新密码'); return }
  loading.value = true
  try {
    await passwordReset({
      target: pwdForm.phone, code: pwdForm.code,
      new_password: pwdForm.newPassword, code_type: 'sms',
    })
    ElMessage.success('密码已重置，请重新登录')
    await userStore.logout()
  } catch (e: any) {
    if (e?.status === 401) ElMessage.error('验证码错误或已过期')
    else ElMessage.error(e?.message || '重置失败')
  } finally { loading.value = false }
}

async function sendEmailCode() {
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(emailForm.email)) { ElMessage.warning('邮箱格式不正确'); return }
  try {
    await sendCode(emailForm.email, 'email')
    ElMessage.success('验证码已发送')
    startCooldown(emailCooldown)
  } catch (e: any) { ElMessage.error(e?.message || '发送失败') }
}

async function handleBindEmail() {
  if (!emailForm.email || !emailForm.code) { ElMessage.warning('请填写邮箱和验证码'); return }
  loading.value = true
  try {
    await bindEmail(emailForm.email, emailForm.code)
    ElMessage.success('邮箱绑定成功')
  } catch (e: any) {
    if (e?.status === 401) ElMessage.error('验证码错误或已过期')
    else if (e?.status === 409) ElMessage.error('该邮箱已被其他账号绑定')
    else ElMessage.error(e?.message || '绑定失败')
  } finally { loading.value = false }
}

async function handleBindWechat() {
  wxLoading.value = true
  try {
    // demo：跳转微信授权，回调后通过 wechat/bind 完成绑定
    const res = await wechatAuth(window.location.origin + '/profile')
    window.location.href = res.data.auth_url
  } catch (e: any) { ElMessage.error(e?.message || '微信授权失败') } finally { wxLoading.value = false }
}


// 013 fresh-eyes P1: wechat bind callback loop
onMounted(async () => {
  const code = route.query.code as string | undefined
  if (!code) return
  wxLoading.value = true
  try {
    const res = await wechatBind(code)
    ElMessage.success(`微信绑定成功（${res.data.openid.slice(0, 8)}...）`)
  } catch (e: any) {
    ElMessage.error(e?.message || '微信绑定失败')
  } finally { wxLoading.value = false }
})

</script>

<style scoped lang="scss">
.profile-card { max-width: 560px; }
.code-row { display: flex; gap: 8px; width: 100%; }
.wechat-pane { text-align: center; padding: 24px 0; }
.muted { color: #909399; font-size: 13px; }
</style>
