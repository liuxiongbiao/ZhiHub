<!-- frontend/src/components/trace/TraceTimeline.vue -->
<template>
  <div class="trace-timeline">
    <div v-if="!trace || trace.steps.length === 0" class="empty">暂无执行轨迹</div>
    <div v-else class="timeline">
      <!-- 总耗时 -->
      <div class="timeline-header">
        <span class="total-time">总耗时: {{ trace.total_elapsed_ms }}ms</span>
        <el-tag size="small">{{ trace.intent }}</el-tag>
        <el-tag v-if="trace.sentiment !== 'normal'" type="warning" size="small">{{ trace.sentiment }}</el-tag>
      </div>

      <!-- 步骤列表 -->
      <div v-for="(step, idx) in trace.steps" :key="idx" class="step">
        <div class="step-dot" :class="step.step"></div>
        <div class="step-content">
          <div class="step-header">
            <span class="step-label">{{ step.label }}</span>
            <span v-if="step.elapsed_ms" class="step-time">{{ step.elapsed_ms }}ms</span>
          </div>
          <!-- Router -->
          <div v-if="step.step === 'router'" class="step-detail">
            <span>意图: {{ step.intent }} | 置信度: {{ formatPercent(step.confidence) }}</span>
          </div>
          <!-- Retrieval -->
          <div v-if="step.step === 'retrieval'" class="step-detail">
            <span>召回 {{ step.result_count }} 条 | Top分: {{ step.top_scores?.join(', ') }}</span>
          </div>
          <!-- Agent -->
          <div v-if="step.step === 'agent'" class="step-detail">
            <span>处理节点: {{ step.agent_type }}</span>
          </div>
          <!-- Generation -->
          <div v-if="step.step === 'generation'" class="step-detail">
            <span>引用 {{ step.sources_count }} 个来源 | 回复预览: {{ step.answer_preview }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { AgentTrace } from '@/types/trace'

defineProps<{ trace: AgentTrace | null }>()

/** 小数 → 百分比字符串（如 0.85 → "85%"） */
function formatPercent(v?: number): string {
  if (v === undefined || v === null) return '-'
  return (v * 100).toFixed(0) + '%'
}
</script>

<style scoped>
.trace-timeline { padding: 16px; }
.empty { color: #999; text-align: center; padding: 40px; }
.timeline-header { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #eee; }
.total-time { font-weight: 600; }
.timeline { position: relative; }
.step { display: flex; gap: 12px; padding-bottom: 24px; position: relative; }
.step:not(:last-child)::before { content: ''; position: absolute; left: 7px; top: 16px; bottom: 0; width: 2px; background: #e4e7ed; }
.step-dot { width: 16px; height: 16px; border-radius: 50%; flex-shrink: 0; margin-top: 2px; }
.step-dot.router { background: #409eff; }
.step-dot.retrieval { background: #67c23a; }
.step-dot.agent { background: #e6a23c; }
.step-dot.generation { background: #f56c6c; }
.step-content { flex: 1; }
.step-header { display: flex; justify-content: space-between; align-items: center; }
.step-label { font-weight: 500; font-size: 14px; }
.step-time { font-size: 12px; color: #999; }
.step-detail { font-size: 13px; color: #666; margin-top: 2px; }
</style>
