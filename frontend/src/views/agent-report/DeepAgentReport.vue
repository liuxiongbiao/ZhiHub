<!-- frontend/src/views/agent-report/DeepAgentReport.vue -->
<!-- 职责: DeepAgent开放任务 - 发起报告生成/查看执行步骤/查看报告 -->
<template>
  <div class="deep-agent-report">
    <h2>DeepAgent 报告生成</h2>
    <p class="hint">输入任务目标，DeepAgent 自动规划并执行多步分析</p>

    <!-- 输入区 -->
    <div class="input-area">
      <el-input
        v-model="goal"
        placeholder="例如：生成本周工单积压分析报告"
        size="large"
        clearable
        :disabled="loading"
        @keyup.enter="handleExecute"
      >
        <template #append>
          <el-button type="primary" :loading="loading" @click="handleExecute">执行</el-button>
        </template>
      </el-input>
    </div>

    <!-- 执行中 -->
    <div v-if="loading" class="loading-area">
      <el-icon class="is-loading"><span>⏳</span></el-icon>
      <span>DeepAgent 正在执行中...</span>
    </div>

    <!-- 结果区 -->
    <div v-if="result" class="result-area">
      <!-- 执行步骤 -->
      <div class="steps-section">
        <h4>执行步骤 ({{ result.total_elapsed_ms }}ms)</h4>
        <el-steps :active="result.steps?.length" align-center>
          <el-step
            v-for="s in result.steps"
            :key="s.step"
            :title="s.label"
            :description="`${s.elapsed_ms}ms`"
          />
        </el-steps>

        <!-- 步骤详情 -->
        <el-collapse style="margin-top: 16px">
          <el-collapse-item v-for="s in result.steps" :key="s.step" :title="`Step ${s.step}: ${s.label}`">
            <pre class="step-detail">{{ JSON.stringify(s.result, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>

      <!-- 报告 -->
      <div v-if="result.report" class="report-section">
        <div class="report-head">
          <h4>分析报告</h4>
          <el-button size="small" @click="downloadReport(result.report, 'deep-agent-report.md')">下载报告</el-button>
        </div>
        <div class="report-content" v-html="renderMarkdown(result.report)"></div>
      </div>

      <!-- 错误 -->
      <el-alert v-if="result.error" :title="result.error" type="error" show-icon />
    </div>

    <!-- 规格 013 审查 -2：历史任务查询（GET /agent/deep-task/{id}） -->
    <el-card shadow="never" class="history-card">
      <template #header><span class="section-title">查询历史任务</span></template>
      <div class="history-row">
        <el-input v-model="taskId" placeholder="输入任务 ID（如 deep-xxxxxxxxxxxx）" style="width: 320px" />
        <el-button type="primary" :loading="querying" @click="handleQueryTask">查询</el-button>
      </div>
      <el-alert v-if="queryError" :title="queryError" type="error" show-icon class="query-error" />
      <div v-if="taskResult?.report" class="report-section">
        <div class="report-head">
          <h4>历史报告</h4>
          <el-button size="small" @click="downloadReport(taskResult.report, `deep-agent-${taskId}.md`)">下载报告</el-button>
        </div>
        <div class="report-content" v-html="renderMarkdown(taskResult.report)"></div>
      </div>
    </el-card>

    <!-- 快捷预设 -->
    <div class="presets">
      <h4>快捷预设</h4>
      <el-button v-for="p in presets" :key="p" size="small" @click="goal = p" :disabled="loading">
        {{ p }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/api/request'

/** 下载报告为 Markdown 文件（PRD §3.11） */
function downloadReport(text: string, filename: string) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const goal = ref('')
const loading = ref(false)
const result = ref<any>(null)
const taskId = ref('')
const querying = ref(false)
const queryError = ref('')
const taskResult = ref<any>(null)

/** 快捷预设任务目标列表 */
const presets = [
  '生成本周工单积压分析报告',
  '分析本月订单退款趋势',
  '总结合约客户常见问题',
]

/** 极简 Markdown 渲染（换行/标题/列表项） */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 极简 Markdown 渲染（换行/标题/列表项），先 HTML 转义再替换，防存储型 XSS */
function renderMarkdown(text: string): string {
  return escapeHtml(text)
    .replace(/\n/g, '<br>')
    .replace(/## (.+)/g, '<h4>$1</h4>')
    .replace(/- (.+)/g, '• $1')
}

/** 提交任务目标，调 /agent/deep-task 执行并展示结果 */
async function handleExecute() {
  if (!goal.value.trim()) return
  loading.value = true
  result.value = null
  try {
    // DeepAgent 同步执行 3 步规划（真实 LLM 多次调用），需远大于全局 10s 超时
    const res = await request.post('/agent/deep-task', { goal: goal.value.trim() }, { timeout: 300000 })
    // DeepAgent 为 Celery 异步任务：轮询 GET 直到 completed/failed（最多 3 分钟）
    let current = res.data
    const deadline = Date.now() + 180000
    while ((current.status === 'pending' || current.status === 'running') && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 3000))
      const q = await request.get(`/agent/deep-task/${current.task_id}`, { timeout: 10000 })
      current = q.data
    }
    result.value = current
  } catch (e: any) {
    ElMessage.error(e?.message || '执行失败')
  } finally {
    loading.value = false
  }
}

/** 查询历史 DeepAgent 任务（GET /agent/deep-task/{id}，按租户隔离） */
async function handleQueryTask() {
  if (!taskId.value.trim()) { ElMessage.warning('请输入任务 ID'); return }
  querying.value = true
  queryError.value = ''
  taskResult.value = null
  try {
    const res = await request.get(`/agent/deep-task/${taskId.value.trim()}`)
    taskResult.value = res.data
    if (!res.data?.report) ElMessage.info('任务存在但尚未生成报告')
  } catch (e: any) {
    if (e?.status === 404) queryError.value = '任务不存在或已过期'
    else queryError.value = e?.message || '查询失败'
  } finally { querying.value = false }
}
</script>

<style scoped>
.deep-agent-report { max-width: 900px; margin: 20px auto; padding: 20px; }
.hint { color: #999; font-size: 13px; margin-bottom: 16px; }
.input-area { margin-bottom: 20px; }
.loading-area { text-align: center; padding: 40px; font-size: 16px; color: #666; }
.result-area { margin-top: 24px; }
.steps-section { margin-bottom: 24px; }
.step-detail { font-size: 12px; background: #f5f5f5; padding: 8px; border-radius: 4px; overflow-x: auto; }
.report-section { margin-top: 24px; }
.report-head { display: flex; align-items: center; justify-content: space-between; }
.report-content { background: #fafafa; padding: 20px; border-radius: 8px; line-height: 1.8; }
.presets { margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; }
.presets h4 { margin-bottom: 8px; }
.presets .el-button { margin: 4px; }
</style>
