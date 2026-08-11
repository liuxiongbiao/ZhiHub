<!-- frontend/src/views/agent-report/DeepAgentReport.vue -->
<!-- 职责: DeepAgent开放任务 - 发起报告生成/查看执行步骤/查看报告 -->
<!-- 分工: 🟢 弱模型可独立完成 -->
<!-- 对应日程: Day 8 -->
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
        <h4>分析报告</h4>
        <div class="report-content" v-html="renderMarkdown(result.report)"></div>
      </div>

      <!-- 错误 -->
      <el-alert v-if="result.error" :title="result.error" type="error" show-icon />
    </div>

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

const goal = ref('')
const loading = ref(false)
const result = ref<any>(null)

/** 快捷预设任务目标列表 */
const presets = [
  '生成本周工单积压分析报告',
  '分析本月订单退款趋势',
  '总结合约客户常见问题',
]

/** 极简 Markdown 渲染（换行/标题/列表项） */
function renderMarkdown(text: string): string {
  return text.replace(/\n/g, '<br>').replace(/## (.+)/g, '<h4>$1</h4>').replace(/- (.+)/g, '• $1')
}

/** 提交任务目标，调 /agent/deep-task 执行并展示结果 */
async function handleExecute() {
  if (!goal.value.trim()) return
  loading.value = true
  result.value = null
  try {
    // DeepAgent 同步执行 3 步规划（真实 LLM 多次调用），需远大于全局 10s 超时
    const res = await request.post('/agent/deep-task', { goal: goal.value.trim() }, { timeout: 300000 })
    result.value = res.data
  } catch (e: any) {
    ElMessage.error(e?.message || '执行失败')
  } finally {
    loading.value = false
  }
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
.report-content { background: #fafafa; padding: 20px; border-radius: 8px; line-height: 1.8; }
.presets { margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; }
.presets h4 { margin-bottom: 8px; }
.presets .el-button { margin: 4px; }
</style>
