<template>
  <div class="trace-timeline">
    <div v-if="!trace || !trace.steps?.length" class="empty-trace"><span>◎</span><div><strong>还没有可展示的决策轨迹</strong><p>客户与 AI 完成一轮对话后，这里会说明系统为什么这样回答。</p></div></div>
    <template v-else>
      <header class="trace-head"><div><p class="eyebrow">决策过程</p><strong>这次服务是怎样完成的</strong></div><div class="trace-meta"><span class="mono-data">{{ trace.total_elapsed_ms }}ms</span><el-switch v-model="expertMode" size="small" inline-prompt active-text="专家" inactive-text="业务" /></div></header>
      <div class="summary-strip"><span>{{ intentLabel(trace.intent) }}</span><span>置信度 {{ formatPercent(trace.confidence) }}</span><span v-if="trace.sentiment !== 'normal'">情绪：{{ sentimentLabel(trace.sentiment) }}</span></div>

      <div class="timeline">
        <article v-for="(step, index) in trace.steps" :key="`${step.step}-${index}`" class="step" :class="step.step">
          <div class="step-marker"><span>{{ index + 1 }}</span></div>
          <div class="step-content">
            <div class="step-header"><strong>{{ businessLabel(step) }}</strong><span v-if="step.elapsed_ms !== null && step.elapsed_ms !== undefined" class="mono-data">{{ step.elapsed_ms }}ms</span></div>
            <p>{{ businessDetail(step) }}</p>
            <div v-if="expertMode" class="expert-detail">
              <div><span>技术节点</span><code>{{ step.technical_label || step.step }}</code></div>
              <div v-if="step.intent"><span>识别意图</span><code>{{ step.intent }}</code></div>
              <div v-if="step.query"><span>检索查询</span><code>{{ step.query }}</code></div>
              <div v-if="step.top_scores?.length"><span>Top 分数</span><code>{{ step.top_scores.join(' · ') }}</code></div>
              <div v-if="step.agent_type"><span>Agent</span><code>{{ step.agent_type }}</code></div>
            </div>
          </div>
        </article>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { AgentTrace, TraceStep } from '@/types/trace'
import { traceIntentLabel as intentLabel } from '@/labels'
defineProps<{ trace: AgentTrace | null }>()
const expertMode = ref(false)
function formatPercent(value?: number) { return value === undefined || value === null ? '—' : `${Math.round(value * 100)}%` }
function sentimentLabel(value?: string) { return ({ normal: '平稳', unhappy: '不满', negative: '负面', angry: '愤怒' } as Record<string,string>)[value || ''] || value || '平稳' }
function businessLabel(step: TraceStep) {
  if (step.label && !step.label.startsWith('Agent:')) return step.label
  return ({ router: '理解客户诉求', retrieval: '查找可信知识依据', agent: '执行业务处理', tool: '查询业务系统', safety: '执行服务安全规则', handoff: '交给人工继续处理', generation: '组织并交付答复' } as Record<string,string>)[step.step] || '完成处理'
}
function businessDetail(step: TraceStep) {
  if (step.observation) return step.observation
  if (step.step === 'router') return `识别为「${intentLabel(step.intent || '')}」，判断把握 ${formatPercent(step.confidence)}。`
  if (step.step === 'retrieval') return `从企业知识中找到 ${step.result_count || 0} 条依据，再筛选最相关内容。`
  if (step.step === 'agent' || step.step === 'tool') return step.agent_type?.includes('order') ? '已按租户权限查询订单业务系统。' : step.agent_type?.includes('ticket') ? '已按租户权限查询工单业务系统。' : '已调用对应业务能力完成处理。'
  if (step.step === 'safety') return '当前问题涉及风险边界，系统没有自动作出不可逆承诺。'
  if (step.step === 'handoff') return '对话、业务信息和判断原因已一起交给坐席。'
  if (step.step === 'generation') return `依据 ${step.sources_count || 0} 个可信来源组织答复${step.answer_preview ? `：“${step.answer_preview}”` : '。'}`
  return '该步骤已完成。'
}
</script>

<style scoped>
.trace-timeline { color: var(--zh-text-primary); }.empty-trace { display: flex; gap: 13px; align-items: center; min-height: 120px; padding: 18px; color: var(--zh-text-secondary); background: #f5f7f5; border-radius: 11px; }.empty-trace > span { display: grid; place-items: center; width: 34px; height: 34px; flex: 0 0 auto; color: var(--zh-primary); background: var(--zh-primary-bg); border-radius: 10px; }.empty-trace strong { color: var(--zh-text-regular); font-size: 11px; }.empty-trace p { margin-top: 4px; font-size: 9px; line-height: 1.5; }.trace-head { display: flex; justify-content: space-between; gap: 12px; align-items: center; }.trace-head strong { font-size: 13px; }.trace-meta { display: flex; gap: 9px; align-items: center; color: var(--zh-text-secondary); font-size: 9px; }.summary-strip { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0 16px; }.summary-strip span { padding: 4px 7px; color: var(--zh-text-regular); font-size: 8px; background: #f0f4f2; border-radius: 6px; }.step { position: relative; display: grid; grid-template-columns: auto 1fr; gap: 11px; padding-bottom: 16px; }.step:not(:last-child)::before { content: ''; position: absolute; left: 12px; top: 24px; bottom: 0; border-left: 1px dashed #bfd0cc; }.step-marker { position: relative; z-index: 1; display: grid; place-items: center; width: 25px; height: 25px; color: #fff; font: 700 8px "Cascadia Code",monospace; background: var(--zh-primary); border: 4px solid var(--zh-bg-card); border-radius: 50%; }.step.safety .step-marker, .step.handoff .step-marker { background: var(--zh-amber); }.step-content { min-width: 0; padding-top: 3px; }.step-header { display: flex; justify-content: space-between; gap: 8px; }.step-header strong { font-size: 10px; }.step-header span { color: var(--zh-text-placeholder); font-size: 8px; }.step-content > p { margin-top: 4px; color: var(--zh-text-regular); font-size: 9px; line-height: 1.6; }.expert-detail { display: grid; gap: 5px; margin-top: 8px; padding: 8px; background: #f3f6f4; border-radius: 7px; }.expert-detail div { display: grid; grid-template-columns: 62px 1fr; gap: 6px; }.expert-detail span { color: var(--zh-text-secondary); font-size: 8px; }.expert-detail code { overflow: hidden; color: #34515a; font: 8px "Cascadia Code",monospace; text-overflow: ellipsis; white-space: nowrap; }
</style>
