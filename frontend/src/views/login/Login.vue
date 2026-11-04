<template>
  <main class="login-page">
    <section class="brand-panel">
      <div class="brand-mark"><span class="mark-core">枢</span><span>智枢 ZhiHub</span></div>
      <div class="brand-copy">
        <p class="eyebrow">企业客服中枢</p>
        <h1>让每一次客户咨询，<br><em>走完服务闭环。</em></h1>
        <p class="brand-lead">从可信知识回答，到订单与工单处理，再到风险场景的人工接管——所有过程可见、可控、可追溯。</p>
      </div>
      <ServiceFlowRail active="handoff" />
      <div class="proof-row">
        <span><i></i> 知识有依据</span>
        <span><i></i> 业务能执行</span>
        <span><i></i> 人工接得住</span>
      </div>
    </section>

    <section class="login-panel">
      <div class="login-shell">
        <header class="login-head">
          <p class="eyebrow">欢迎回来</p>
          <h2>进入服务控制台</h2>
          <p>继续处理客户问题和运营信号</p>
        </header>

        <div v-if="options.demo_quick_login" class="demo-entry" data-test="demo-login">
          <div class="demo-head">
            <div><strong>本机演示环境</strong><span>无需填写或保存演示密码</span></div>
            <el-tag type="success" effect="light" size="small">安全快速登录</el-tag>
          </div>
          <div class="persona-grid">
            <button
              v-for="persona in options.demo_personas"
              :key="persona"
              type="button"
              class="persona-button"
              :disabled="demoLoading !== ''"
              @click="handleDemoLogin(persona)"
            >
              <span class="persona-avatar">{{ persona === 'admin' ? '管' : persona.slice(-1) }}</span>
              <span><strong>{{ DEMO_ROLE_META[persona].label }}</strong><small>{{ DEMO_ROLE_META[persona].description }}</small></span>
              <span v-if="demoLoading === persona" class="demo-spinner">•••</span>
              <span v-else aria-hidden="true">→</span>
            </button>
          </div>
          <div class="or-line"><span>或使用正式账号</span></div>
        </div>

        <el-tabs v-model="activeTab" class="login-tabs">
          <el-tab-pane label="账号密码" name="password">
            <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-position="top" @submit.prevent="handlePwdLogin">
              <el-form-item label="手机号" prop="phone"><el-input v-model="pwdForm.phone" placeholder="请输入手机号" size="large" /></el-form-item>
              <el-form-item label="密码" prop="password"><el-input v-model="pwdForm.password" type="password" show-password placeholder="请输入密码" size="large" /></el-form-item>
              <el-button class="submit-button" type="primary" native-type="submit" size="large" :loading="loading">登录控制台</el-button>
            </el-form>
          </el-tab-pane>

          <el-tab-pane v-if="options.email_login" label="邮箱登录" name="email">
            <el-form ref="emailFormRef" :model="emailForm" :rules="emailRules" label-position="top" @submit.prevent="handleEmailLogin">
              <el-form-item label="邮箱" prop="email"><el-input v-model="emailForm.email" placeholder="请输入邮箱" size="large" /></el-form-item>
              <el-form-item label="密码" prop="password"><el-input v-model="emailForm.password" type="password" show-password placeholder="请输入密码" size="large" /></el-form-item>
              <el-button class="submit-button" type="primary" native-type="submit" size="large" :loading="loading">登录控制台</el-button>
            </el-form>
          </el-tab-pane>

          <el-tab-pane v-if="options.sms_login" label="手机验证码" name="sms">
            <el-form ref="smsFormRef" :model="smsForm" :rules="smsRules" label-position="top" @submit.prevent="handleSmsLogin">
              <el-form-item label="手机号" prop="phone"><el-input v-model="smsForm.phone" placeholder="请输入手机号" size="large" /></el-form-item>
              <el-form-item label="验证码" prop="code">
                <div class="code-row"><el-input v-model="smsForm.code" placeholder="请输入验证码" size="large" /><el-button :disabled="codeCooldown > 0" size="large" @click="sendSmsCode">{{ codeCooldown > 0 ? `${codeCooldown}s` : '获取验证码' }}</el-button></div>
              </el-form-item>
              <el-button class="submit-button" type="primary" native-type="submit" size="large" :loading="loading">验证并登录</el-button>
            </el-form>
          </el-tab-pane>

          <el-tab-pane v-if="options.wechat_login" label="微信扫码" name="wechat">
            <div class="wechat-pane"><div class="qr-placeholder">微信</div><el-button type="success" :loading="wechatLoading" @click="handleWechatLogin">微信扫码登录</el-button><p>点击后进入已配置的微信授权流程</p></div>
          </el-tab-pane>
        </el-tabs>

      <p class="switch-link">还没有账号？<router-link to="/register">注册并创建企业空间</router-link></p>
        <p class="security-note">登录即进入租户隔离空间 · 敏感操作全程审计</p>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useUserStore } from '@/store/modules/user'
import { homePathForRole } from '@/permission'
import { getLoginOptions, smsSend, wechatAuth, wechatCallback } from '@/api/auth'
import { DEMO_ROLE_META } from '@/api/demo'
import type { DemoPersona, LoginOptionsResponse } from '@/types/auth'
import ServiceFlowRail from '@/components/common/ServiceFlowRail.vue'
import { startCooldown } from '@/utils/cooldown'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const loading = ref(false)
const demoLoading = ref<DemoPersona | ''>('')
const activeTab = ref('password')
const codeCooldown = ref(0)
const wechatLoading = ref(false)
const options = reactive<LoginOptionsResponse>({
  email_login: true,
  sms_login: true,
  wechat_login: true,
  payment: false,
  demo_quick_login: false,
  demo_personas: [],
})

const pwdFormRef = ref<FormInstance>()
const emailFormRef = ref<FormInstance>()
const smsFormRef = ref<FormInstance>()
const pwdForm = reactive({ phone: '', password: '' })
const emailForm = reactive({ email: '', password: '' })
const smsForm = reactive({ phone: '', code: '' })
const phoneRule = [{ required: true, message: '请输入手机号', trigger: 'blur' }, { pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' }]
const pwdRules: FormRules = { phone: phoneRule, password: [{ required: true, message: '请输入密码', trigger: 'blur' }, { min: 8, message: '密码至少 8 位', trigger: 'blur' }] }
const emailRules: FormRules = { email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }, { type: 'email', message: '邮箱格式不正确', trigger: 'blur' }], password: pwdRules.password }
const smsRules: FormRules = { phone: phoneRule, code: [{ required: true, message: '请输入验证码', trigger: 'blur' }] }

function safeRedirect() {
  const redirect = route.query.redirect
  const fallback = homePathForRole(userStore.role)
  const safe = typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : fallback
  router.push(safe)
}

async function handleDemoLogin(persona: DemoPersona) {
  demoLoading.value = persona
  try {
    await userStore.loginDemo(persona)
    ElMessage.success(`已进入${DEMO_ROLE_META[persona].label}演示视角`)
    router.push(persona === 'admin' ? '/demo-center' : '/agent/console')
  } catch (error: any) {
    ElMessage.error(error?.message || '演示登录暂不可用')
  } finally { demoLoading.value = '' }
}

async function submitCredentialLogin(form: FormInstance | undefined, login: () => Promise<unknown>) {
  if (!form) return
  try { await form.validate() } catch { return }
  loading.value = true
  try { await login(); ElMessage.success('登录成功'); safeRedirect() }
  catch (error: any) { ElMessage.error(error?.message || '登录失败') }
  finally { loading.value = false }
}

async function handlePwdLogin() {
  await submitCredentialLogin(pwdFormRef.value, () => userStore.login(pwdForm))
}

async function handleEmailLogin() {
  await submitCredentialLogin(emailFormRef.value, () => userStore.loginEmail(emailForm))
}

async function sendSmsCode() {
  if (!/^1[3-9]\d{9}$/.test(smsForm.phone)) { ElMessage.warning('请先输入正确的手机号'); return }
  try {
    await smsSend(smsForm.phone)
    ElMessage.success('验证码已发送')
    startCooldown(codeCooldown)
  } catch (error: any) { ElMessage.error(error?.message || '验证码发送失败') }
}

async function handleSmsLogin() {
  await submitCredentialLogin(smsFormRef.value, () => userStore.loginSms(smsForm))
}

async function handleWechatLogin() {
  wechatLoading.value = true
  try { const response = await wechatAuth(window.location.origin + '/login'); window.location.href = response.data.auth_url }
  catch (error: any) { ElMessage.error(error?.message || '微信授权失败') }
  finally { wechatLoading.value = false }
}

async function handleWechatCallback() {
  const code = typeof route.query.code === 'string' ? route.query.code : undefined
  const state = typeof route.query.state === 'string' ? route.query.state : undefined
  if (!code) return
  wechatLoading.value = true
  try {
    const response = await wechatCallback(code, state)
    const data = response.data
    if (data.need_bind) ElMessage.warning('该微信尚未绑定账号，请先登录后在个人中心完成绑定')
    else if (data.access_token && data.refresh_token && data.user_id && data.tenant_id && data.role) {
      userStore.$patch({ token: data.access_token, refresh_token: data.refresh_token, user_id: data.user_id, tenant_id: data.tenant_id, role: data.role as 'admin' | 'agent' })
      userStore.persist(); ElMessage.success('微信登录成功'); safeRedirect()
    }
  } catch (error: any) { ElMessage.error(error?.message || '微信授权失败') }
  finally { wechatLoading.value = false }
}

onMounted(async () => {
  try { Object.assign(options, (await getLoginOptions()).data) } catch { /* 能力探测失败时保留兼容入口 */ }
  await handleWechatCallback()
})
</script>

<style scoped>
.login-page { min-height: 100vh; display: grid; grid-template-columns: minmax(440px, 1.15fr) minmax(430px, .85fr); background: #f5f7f4; }
.brand-panel { position: relative; overflow: hidden; display: flex; flex-direction: column; justify-content: space-between; padding: clamp(34px, 5vw, 72px); color: #fff; background: var(--zh-ink); }
.brand-panel::before { content: ''; position: absolute; width: 36rem; height: 36rem; right: -17rem; top: -15rem; border: 1px solid rgba(24,183,160,.24); border-radius: 50%; box-shadow: 0 0 0 70px rgba(24,183,160,.025), 0 0 0 140px rgba(24,183,160,.018); }
.brand-panel::after { content: ''; position: absolute; inset: 0; opacity: .16; pointer-events: none; background-image: linear-gradient(rgba(255,255,255,.06) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.05) 1px, transparent 1px); background-size: 52px 52px; mask-image: linear-gradient(to bottom, transparent, #000 30%, transparent); }
.brand-mark, .brand-copy, .brand-panel :deep(.service-rail), .proof-row { position: relative; z-index: 1; }
.brand-mark { display: flex; align-items: center; gap: 12px; font-weight: 760; letter-spacing: .02em; }
.mark-core { display: grid; place-items: center; width: 35px; height: 35px; color: #08201f; background: var(--zh-signal); border-radius: 10px 10px 10px 3px; }
.brand-copy { max-width: 690px; margin: 10vh 0 7vh; }
.brand-copy h1 { margin: 16px 0 22px; font-size: clamp(38px, 5vw, 70px); line-height: 1.08; letter-spacing: -.055em; }
.brand-copy h1 em { color: #8ee9db; font-style: normal; }
.brand-lead { max-width: 570px; color: #a9bec2; font-size: clamp(15px, 1.4vw, 18px); line-height: 1.9; }
.proof-row { display: flex; gap: 24px; margin-top: 24px; color: #91a9ad; font-size: 12px; }
.proof-row span { display: flex; align-items: center; gap: 7px; }
.proof-row i { width: 6px; height: 6px; background: var(--zh-signal); border-radius: 50%; box-shadow: 0 0 0 4px rgba(24,183,160,.12); }
.login-panel { display: grid; place-items: center; padding: 36px clamp(22px, 5vw, 72px); }
.login-shell { width: min(100%, 500px); }
.login-head { margin-bottom: 24px; }
.login-head h2 { margin: 9px 0 7px; font-size: 30px; letter-spacing: -.035em; }
.login-head p:last-child { color: var(--zh-text-secondary); }
.demo-entry { margin-bottom: 16px; padding: 15px; border: 1px solid #bfe1da; border-radius: var(--zh-radius); background: linear-gradient(145deg, #f8fffc, #edf8f4); }
.demo-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.demo-head div { display: grid; }
.demo-head strong { font-size: 13px; }
.demo-head span { margin-top: 3px; color: var(--zh-text-secondary); font-size: 11px; }
.persona-grid { display: grid; gap: 7px; margin-top: 12px; }
.persona-button { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center; width: 100%; padding: 9px 10px; text-align: left; color: var(--zh-text-primary); background: rgba(255,255,255,.78); border: 1px solid #d8e9e5; border-radius: 10px; cursor: pointer; transition: border .18s, transform .18s; }
.persona-button:hover { transform: translateX(3px); border-color: var(--zh-primary); }
.persona-button > span:nth-child(2) { display: grid; }
.persona-button small { color: var(--zh-text-secondary); font-size: 10px; }
.persona-avatar { display: grid; place-items: center; width: 28px; height: 28px; color: #fff; font-size: 11px; font-weight: 800; background: var(--zh-ink-soft); border-radius: 9px; }
.or-line { display: flex; align-items: center; gap: 10px; margin: 14px 0 -7px; color: var(--zh-text-placeholder); font-size: 10px; }
.or-line::before, .or-line::after { content: ''; flex: 1; border-top: 1px solid #d9e7e3; }
.login-tabs :deep(.el-tabs__nav-wrap::after) { height: 1px; background: var(--zh-border-light); }
.login-tabs :deep(.el-tabs__item) { padding: 0 14px; font-size: 13px; }
.submit-button { width: 100%; margin-top: 4px; }
.code-row { display: flex; gap: 8px; width: 100%; }
.wechat-pane { display: grid; justify-items: center; gap: 13px; padding: 22px 0; color: var(--zh-text-secondary); font-size: 12px; }
.qr-placeholder { display: grid; place-items: center; width: 104px; height: 104px; color: #fff; font-weight: 800; background: repeating-linear-gradient(45deg, #1b3437, #1b3437 7px, #2b4b4d 7px, #2b4b4d 14px); border: 8px solid #fff; box-shadow: 0 0 0 1px var(--zh-border); }
.switch-link { margin-top: 18px; text-align: center; font-size: 13px; }
.security-note { margin-top: 22px; color: var(--zh-text-placeholder); font-size: 10px; text-align: center; }
@media (max-width: 960px) { .login-page { grid-template-columns: 1fr; } .brand-panel { min-height: 450px; } .brand-copy { margin: 65px 0; } }
@media (max-width: 560px) { .brand-panel { display: none; } .login-panel { padding: 24px 18px; } }
</style>
