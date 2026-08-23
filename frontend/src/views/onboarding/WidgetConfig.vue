<!-- frontend/src/views/onboarding/WidgetConfig.vue -->
<!-- 规格 013: Widget 品牌定制（PRD §3.8 White-label） -->
<!-- : 完整接入 API Key 轮换（仅生成时显示一次）、域名白名单查询/修改、
     品牌配置（主题色/Logo/欢迎语）、可部署嵌入代码（绝对地址）。
     API Key 仅存组件内存，不写 localStorage/日志。 -->
<template>
  <div>
    <PageHeader title="Widget 品牌定制" subtitle="配置 API Key、域名白名单、主题色与欢迎语，并获取可部署的嵌入代码" />
    <div class="page-container">

      <!-- API Key 轮换 -->
      <el-card shadow="never" class="config-card">
        <template #header><span class="section-title">Widget API Key</span></template>
        <el-alert type="warning" :closable="false" show-icon class="key-alert"
          title="Key 仅在生成时显示一次；轮换会使旧 Key 立即失效，已嵌入的网站需更新" />
        <template v-if="apiKey">
          <div class="key-row">
            <code class="key-value">{{ apiKey }}</code>
            <el-button size="small" type="primary" data-test="copy-key-btn" @click="copyKey">复制</el-button>
          </div>
          <p class="muted">请立即复制并妥善保存——离开本页后无法再次查看。</p>
        </template>
        <p v-else class="muted">服务端只保存 Key 哈希，历史 Key 无法找回；需要查看请轮换新 Key。</p>
        <el-button type="danger" plain :loading="rotating" data-test="rotate-key-btn" @click="handleRotateKey">
          {{ apiKey ? '重新生成（旧 Key 立即失效）' : '生成 API Key' }}
        </el-button>
      </el-card>

      <!-- 域名白名单 -->
      <el-card shadow="never" class="config-card">
        <template #header><span class="section-title">域名白名单</span></template>
        <el-input v-model="domainsText" type="textarea" :rows="3"
          placeholder="example.com, shop.example.com（逗号分隔；精确或子域名匹配）" />
        <p class="muted">仅白名单内域名（含子域名）可加载 Widget；留空时仅开发环境 localhost 可用。</p>
        <el-button type="primary" plain :loading="savingDomains" data-test="save-domains-btn" @click="handleSaveDomains">
          保存域名白名单
        </el-button>
      </el-card>

      <!-- 品牌配置 -->
      <el-card shadow="never" class="config-card">
        <template #header><span class="section-title">品牌配置</span></template>
        <el-form :model="form" label-position="top" @submit.prevent="handleSave">
          <el-form-item label="主题色">
            <el-color-picker v-model="form.theme_color" />
            <span class="color-hint">{{ form.theme_color }}</span>
          </el-form-item>
          <el-form-item label="Logo URL">
            <el-input v-model="form.logo_url" placeholder="https://example.com/logo.png" />
          </el-form-item>
          <el-form-item label="欢迎语">
            <el-input v-model="form.welcome_text" placeholder="您好，请问有什么可以帮您？" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" data-test="save-config-btn" native-type="submit">保存配置</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- 嵌入代码 -->
      <el-card shadow="never" class="embed-card">
        <template #header><span class="section-title">嵌入代码</span></template>
        <el-button size="small" :loading="embedLoading" data-test="load-embed-btn" @click="loadEmbedCode">获取嵌入代码</el-button>
        <div v-if="embedCode" class="embed-row">
          <pre class="embed-code">{{ embedCode }}</pre>
          <el-button size="small" @click="copyEmbed">复制</el-button>
        </div>
        <p v-else class="muted">点击上方按钮获取粘贴到客户网站的 script 片段（绝对地址，可直接嵌入外部网站）</p>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  updateWidgetConfig, getWidgetEmbedCode, getWidgetConfig,
  rotateWidgetApiKey, setWidgetDomains,
} from '@/api/tenant'
import PageHeader from '@/components/common/PageHeader.vue'

const form = reactive({ theme_color: '#409eff', logo_url: '', welcome_text: '您好，请问有什么可以帮您？' })
const saving = ref(false)
const embedLoading = ref(false)
const embedCode = ref('')
// : API Key 仅存组件内存，刷新即丢；不写 localStorage、不打日志
const apiKey = ref('')
const rotating = ref(false)
const domainsText = ref('')
const savingDomains = ref(false)

onMounted(loadConfig)

/** 加载品牌配置 + 域名白名单（GET /tenant/widget） */
async function loadConfig() {
  try {
    const res = await getWidgetConfig()
    const d = res.data
    form.theme_color = d.theme_color || form.theme_color
    form.logo_url = d.logo_url || ''
    form.welcome_text = d.welcome_text || form.welcome_text
    domainsText.value = (d.domains || []).join(', ')
  } catch (e: any) {
    ElMessage.error(e?.message || '加载配置失败')
  }
}

/** 轮换 API Key：成功后一次性展示，提示旧 Key 立即失效 */
async function handleRotateKey() {
  rotating.value = true
  try {
    const res = await rotateWidgetApiKey()
    apiKey.value = res.data.api_key
    ElMessage.success('新 Key 已生成，旧 Key 已立即失效')
  } catch (e: any) {
    ElMessage.error(e?.message || '生成失败')
  } finally { rotating.value = false }
}

async function copyKey() {
  try {
    await navigator.clipboard.writeText(apiKey.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('复制失败，请手动选中文本复制')
  }
}

/** 保存域名白名单：逗号分隔文本 → 数组 */
async function handleSaveDomains() {
  savingDomains.value = true
  try {
    const domains = domainsText.value.split(',').map((d) => d.trim()).filter(Boolean)
    await setWidgetDomains(domains)
    ElMessage.success('域名白名单已保存')
    domainsText.value = domains.join(', ')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally { savingDomains.value = false }
}

async function handleSave() {
  saving.value = true
  try {
    await updateWidgetConfig({
      theme_color: form.theme_color,
      // : Logo 总是提交——空串 = 明确清除旧 Logo（区别于不提交字段保持原值）
      logo_url: form.logo_url,
      welcome_text: form.welcome_text || undefined,
    })
    ElMessage.success('配置已保存')
    await loadEmbedCode()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally { saving.value = false }
}

async function loadEmbedCode() {
  embedLoading.value = true
  try {
    const res = await getWidgetEmbedCode()
    embedCode.value = res.data.embed_code
  } catch (e: any) {
    ElMessage.error(e?.message || '获取嵌入代码失败')
  } finally { embedLoading.value = false }
}

async function copyEmbed() {
  try {
    await navigator.clipboard.writeText(embedCode.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('复制失败，请手动选中文本复制')
  }
}
</script>

<style scoped lang="scss">
.config-card { max-width: 560px; margin-bottom: var(--zh-gap-lg, 24px); }
.embed-card { max-width: 720px; }
.section-title { font-weight: 600; }
.color-hint { margin-left: 12px; color: #909399; }
.muted { color: #909399; font-size: 13px; margin: 10px 0; }
.key-alert { margin-bottom: 12px; }
.key-row { display: flex; align-items: center; gap: 8px; }
.key-value {
  flex: 1; background: #f5f7fa; padding: 8px 12px; border-radius: 6px;
  font-size: 13px; word-break: break-all;
}
.embed-row { display: flex; align-items: flex-start; gap: 8px; margin-top: 12px; }
.embed-code { flex: 1; background: #f5f7fa; padding: 12px; border-radius: 6px; font-size: 12px; overflow-x: auto; }
</style>
