/**
 * src/views/login/Register.vue
 *
 * 职责: 注册页 —— 支持手机号（短信验证码）与邮箱（邮件激活）两种注册方式（PRD §3.1.1）；
 *      注册成功后跳转后续初始化流程。
 *
 * 依赖: auth API（验证码发送/注册）、router（跳转）。
 */
<template>
  <div class="register-page">
    <el-card class="register-card">
      <h2>注册</h2>
      <el-tabs v-model="regTab" class="reg-tabs">
        <!-- 手机号注册（含短信验证码，PRD §3.1.1） -->
        <el-tab-pane label="手机号注册" name="phone">
          <el-form ref="phoneFormRef" :model="phoneForm" :rules="phoneRules" label-position="top" @submit.prevent="handlePhoneRegister">
            <el-form-item label="手机号" prop="phone">
              <el-input v-model="phoneForm.phone" placeholder="请输入手机号" />
            </el-form-item>
            <el-form-item label="验证码" prop="code">
              <div class="code-row">
                <el-input v-model="phoneForm.code" placeholder="请输入验证码" />
                <el-button :disabled="regCooldown > 0" @click="sendRegCode">
                  {{ regCooldown > 0 ? `${regCooldown}s` : '获取验证码' }}
                </el-button>
              </div>
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input v-model="phoneForm.password" type="password" placeholder="请输入密码" />
            </el-form-item>
            <el-form-item label="企业名称" prop="tenant_name">
              <el-input v-model="phoneForm.tenant_name" placeholder="请输入企业名称" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" native-type="submit" :loading="loading">注册</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- 邮箱注册（PRD §3.1.1） -->
        <el-tab-pane label="邮箱注册" name="email">
          <el-form ref="emailFormRef" :model="emailForm" :rules="emailRules" label-position="top" @submit.prevent="handleEmailRegister">
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="emailForm.email" placeholder="请输入邮箱" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input v-model="emailForm.password" type="password" placeholder="请输入密码" />
            </el-form-item>
            <el-form-item label="企业名称" prop="tenant_name">
              <el-input v-model="emailForm.tenant_name" placeholder="请输入企业名称" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" native-type="submit" :loading="loading">注册</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>
      </el-tabs>

      <p class="switch-link">
        已有账号？<router-link to="/login">立即登录</router-link>
      </p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useUserStore } from '@/store/modules/user'
import { smsSend, registerEmail } from '@/api/auth'
import { startCooldown } from '@/utils/cooldown'

const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)
const regTab = ref('phone')
const regCooldown = ref(0)
const phoneFormRef = ref<FormInstance>()
const emailFormRef = ref<FormInstance>()

const phoneForm = reactive({ phone: '', code: '', password: '', tenant_name: '' })
const emailForm = reactive({ email: '', password: '', tenant_name: '' })

const phoneRules: FormRules = {
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' },
  ],
  code: [{ required: true, message: '请输入验证码', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
    {
      validator: (_r, v, cb) => {
        if (!/[A-Za-z]/.test(v) || !/\d/.test(v)) return cb(new Error('密码需同时包含字母和数字'))
        cb()
      }, trigger: 'blur',
    },
  ],
  tenant_name: [{ required: true, message: '请输入企业名称', trigger: 'blur' }],
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
  tenant_name: [{ required: true, message: '请输入企业名称', trigger: 'blur' }],
}

async function sendRegCode() {
  if (!/^1[3-9]\d{9}$/.test(phoneForm.phone)) {
    ElMessage.warning('请先输入正确的手机号'); return
  }
  try {
    await smsSend(phoneForm.phone)
    ElMessage.success('验证码已发送')
    startCooldown(regCooldown)
  } catch (e: any) {
    ElMessage.error(e?.message || '验证码发送失败')
  }
}

async function handlePhoneRegister() {
  if (!phoneFormRef.value) return
  try { await phoneFormRef.value.validate() } catch { return }
  loading.value = true
  try {
    await userStore.register({
      phone: phoneForm.phone, password: phoneForm.password,
      tenant_name: phoneForm.tenant_name.trim(), code: phoneForm.code,
    })
    try {
      await userStore.login({ phone: phoneForm.phone, password: phoneForm.password })
      ElMessage.success('注册成功'); router.push('/')
    } catch {
      ElMessage.error('注册成功，请登录'); router.push('/login')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '注册失败')
  } finally { loading.value = false }
}

async function handleEmailRegister() {
  if (!emailFormRef.value) return
  try { await emailFormRef.value.validate() } catch { return }
  loading.value = true
  try {
    const res = await registerEmail({
      email: emailForm.email, password: emailForm.password,
      tenant_name: emailForm.tenant_name.trim(),
    })
    if (res.data.activation_link) {
      ElMessage.success('注册成功，请点击激活链接完成邮箱验证')
    } else {
      ElMessage.success('注册成功，激活邮件已发送，请查收后登录')
    }
    router.push('/login')
  } catch (e: any) {
    ElMessage.error(e?.message || '注册失败')
  } finally { loading.value = false }
}
</script>

<style scoped lang="scss">
.register-page { display: flex; justify-content: center; align-items: center; min-height: 100vh; background: #f5f7fa; }
.register-card { width: 460px; }
h2 { text-align: center; margin-bottom: 24px; }
.switch-link { text-align: center; margin-top: 12px; font-size: 14px; }
.code-row { display: flex; gap: 8px; width: 100%; }
</style>
