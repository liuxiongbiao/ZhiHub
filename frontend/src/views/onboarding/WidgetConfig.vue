<!-- frontend/src/views/onboarding/WidgetConfig.vue -->
<!-- 013: Widget 品牌定制（PRD 3.8 White-label） -->
<template>
  <div>
    <PageHeader title="Widget 品牌定制" subtitle="配置对话 Widget 的主题色、Logo 与欢迎语，并生成嵌入代码" />
    <div class="page-container">
      <el-card shadow="never" class="config-card">
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
            <el-button type="primary" :loading="saving" @click="handleSave">保存配置</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card shadow="never" class="embed-card">
        <template #header><span class="section-title">嵌入代码</span></template>
        <el-button size="small" :loading="embedLoading" @click="loadEmbedCode">获取嵌入代码</el-button>
        <pre v-if="embedCode" class="embed-code">{{ embedCode }}</pre>
        <p v-else class="muted">点击上方按钮获取粘贴到客户网站的 script 片段</p>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { updateWidgetConfig, getWidgetEmbedCode } from '@/api/tenant'
import PageHeader from '@/components/common/PageHeader.vue'

const form = reactive({ theme_color: '#409eff', logo_url: '', welcome_text: '您好，请问有什么可以帮您？' })
const saving = ref(false)
const embedLoading = ref(false)
const embedCode = ref('')

async function handleSave() {
  saving.value = true
  try {
    await updateWidgetConfig({
      theme_color: form.theme_color,
      logo_url: form.logo_url || undefined,
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
</script>

<style scoped lang="scss">
.config-card { max-width: 560px; margin-bottom: var(--zh-gap-lg); }
.embed-card { max-width: 720px; }
.color-hint { margin-left: 12px; color: #909399; }
.embed-code { background: #f5f7fa; padding: 12px; border-radius: 6px; font-size: 12px; overflow-x: auto; }
.muted { color: #909399; font-size: 13px; }
</style>
