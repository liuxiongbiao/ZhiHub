/**
 * src/views/login/Login.vue
 *
 * 职责: 登录页 —— 支持账号密码、邮箱登录与微信扫码三种方式（PRD §3.1）；
 *      登录成功后写入本地登录态并跳转目标页，已登录用户访问本页会被重定向。
 *
 * 依赖: auth API（登录/微信回调）、auth-session（令牌存取）、router（redirect 参数）。
 */
<template>
  <div class="login-page">
    <el-card class="login-card">
      <h2>登录</h2>
      <el-tabs v-model="activeTab" class="login-tabs">
        <!-- 账号密码登录 -->
        <el-tab-pane label="账号密码" name="password">
          <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-position="top" @submit.prevent="handlePwdLogin">
            <el-form-item label="手机号" prop="phone">
              <el-input v-model="pwdForm.phone" placeholder="请输入手机号" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input v-model="pwdForm.password" type="password" placeholder="请输入密码" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" native-type="submit" :loading="loading">登录</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- 邮箱密码登录 -->
        <el-tab-pane label="邮箱登录" name="email">
          <el-form ref="emailFormRef" :model="emailForm" :rules="emailRules" label-position="top" @submit.prevent="handleEmailLogin">
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="emailForm.email" placeholder="请输入邮箱" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input v-model="emailForm.password" type="password" placeholder="请输入密码" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" native-type="submit" :loading="loading">登录</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- 手机验证码登录 (规格 013 US1) -->
        <el-tab-pane label="手机验证码" name="sms">
          <el-form ref="smsFormRef" :model="smsForm" :rules="smsRules" label-position="top" @submit.prevent="handleSmsLogin">
            <el-form-item label="手机号" prop="phone">
              <el-input v-model="smsForm.phone" placeholder="请输入手机号" />
            </el-form-item>
            <el-form-item label="验证码" prop="code">
              <div class="code-row">
                <el-input v-model="smsForm.code" placeholder="请输入验证码" />
                <el-button :disabled="codeCooldown > 0" @click="sendSmsCode('sms')">
                  {{ codeCooldown > 0 ? `${codeCooldown}s` : '获取验证码' }}
                </el-button>
              </div>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" native-type="submit" :loading="loading">登录</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- 微信扫码登录 (规格 013 US1) -->
        <el-tab-pane label="微信扫码" name="wechat">
          <div class="wechat-pane">
            <el-button type="success" :loading="wechatLoading" @click="handleWechatLogin">微信扫码登录</el-button>
            <p class="wechat-tip">点击获取微信授权二维码</p>
          </div>
        </el-tab-pane>
      </el-tabs>

      <p class="switch-link">
        还没有账号？<router-link to="/register">立即注册</router-link>
      </p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useUserStore } from '@/store/modules/user'
import { homePathForRole } from '@/permission'
import { smsSend, wechatAuth, wechatCallback } from '@/api/auth'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const loading = ref(false)
const activeTab = ref('password')
const codeCooldown = ref(0)
const wechatLoading = ref(false)

const pwdFormRef = ref<FormInstance>()
const emailFormRef = ref<FormInstance>()
const smsFormRef = ref<FormInstance>()
const pwdForm = reactive({ phone: '', password: '' })
const emailForm = reactive({ email: '', password: '' })
const smsForm = reactive({ phone: '', code: '' })

const phoneRule = [
  { required: true, message: '请输入手机号', trigger: 'blur' },
  { pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' },
]
const pwdRules: FormRules = {
  phone: phoneRule,
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
}
const emailRules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
}
const smsRules: FormRules = {
  phone: phoneRule,
  code: [{ required: true, message: '请输入验证码', trigger: 'blur' }],
}

// : 合法 redirect（以单个 / 开头，拒 // 开放重定向）优先；否则按角色回默认首页
// （admin → /，agent → /agent/console），密码/邮箱/短信/微信回调四条链路共用。
//  收尾一: query.redirect 可能是 string / string[]（重复参数）/ null / undefined，
// 仅 typeof === 'string' 才允许参与判断，其余类型一律回退角色首页，不产生运行时错误。
function safeRedirect() {
  const redirect = route.query.redirect
  const fallback = homePathForRole(userStore.role)
  const safe = typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : fallback
  router.push(safe)
}

async function handlePwdLogin() {
  if (!pwdFormRef.value) return
  try { await pwdFormRef.value.validate() } catch { return }
  loading.value = true
  try {
    await userStore.login({ phone: pwdForm.phone, password: pwdForm.password })
    ElMessage.success('登录成功')
    safeRedirect()
  } catch (e: any) {
    ElMessage.error(e?.message || '登录失败')
  } finally { loading.value = false }
}

async function handleEmailLogin() {
  if (!emailFormRef.value) return
  try { await emailFormRef.value.validate() } catch { return }
  loading.value = true
  try {
    await userStore.loginEmail({ email: emailForm.email, password: emailForm.password })
    ElMessage.success('登录成功')
    safeRedirect()
  } catch (e: any) {
    ElMessage.error(e?.message || '登录失败')
  } finally { loading.value = false }
}

async function sendSmsCode(_scene: string) {
  if (!/^1[3-9]\d{9}$/.test(smsForm.phone)) {
    ElMessage.warning('请先输入正确的手机号'); return
  }
  try {
    await smsSend(smsForm.phone)
    ElMessage.success('验证码已发送')
    codeCooldown.value = 60
    const t = setInterval(() => {
      codeCooldown.value--
      if (codeCooldown.value <= 0) clearInterval(t)
    }, 1000)
  } catch (e: any) {
    ElMessage.error(e?.message || '验证码发送失败')
  }
}

async function handleSmsLogin() {
  if (!smsFormRef.value) return
  try { await smsFormRef.value.validate() } catch { return }
  loading.value = true
  try {
    await userStore.loginSms({ phone: smsForm.phone, code: smsForm.code })
    ElMessage.success('登录成功')
    safeRedirect()
  } catch (e: any) {
    ElMessage.error(e?.message || '登录失败')
  } finally { loading.value = false }
}

async function handleWechatLogin() {
  wechatLoading.value = true
  try {
    const res = await wechatAuth(window.location.origin + '/login')
    const url = res.data.auth_url
    window.location.href = url
  } catch (e: any) {
    ElMessage.error(e?.message || '微信授权失败')
  } finally { wechatLoading.value = false }
}


// 微信扫码回调死循环防护：回调页面直接落地登录态，不再二次跳转（PRD §3.1）
onMounted(async () => {
  const code = route.query.code as string | undefined
  const state = route.query.state as string | undefined
  if (!code) return
  wechatLoading.value = true
  try {
    const res = await wechatCallback(code, state)
    if (res.data.need_bind) {
      ElMessage.warning('该微信尚未绑定账号，请先登录后在个人中心完成绑定')
    } else if (res.data.access_token && res.data.refresh_token) {
      userStore.$patch({
        token: res.data.access_token,
        refresh_token: res.data.refresh_token,
        user_id: res.data.user_id,
        tenant_id: res.data.tenant_id,
        role: (res.data.role ?? 'admin') as 'admin' | 'agent',
      })
      userStore.persist()
      ElMessage.success('微信登录成功')
      safeRedirect()
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '微信授权失败')
  } finally { wechatLoading.value = false }
})

</script>

<style scoped lang="scss">
.login-page { display: flex; justify-content: center; align-items: center; min-height: 100vh; background: #f5f7fa; }
.login-card { width: 420px; }
h2 { text-align: center; margin-bottom: 24px; }
.switch-link { text-align: center; margin-top: 12px; font-size: 14px; }
.code-row { display: flex; gap: 8px; width: 100%; }
.wechat-pane { text-align: center; padding: 24px 0; }
.wechat-tip { margin-top: 12px; font-size: 13px; color: #909399; }
</style>
