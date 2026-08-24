<template>
  <button class="signal-metric" :class="tone" type="button" @click="$emit('click')">
    <span class="metric-top">
      <span class="metric-label">{{ label }}</span>
      <span v-if="trend" class="metric-trend">{{ trend }}</span>
    </span>
    <strong class="metric-value mono-data">{{ value }}</strong>
    <span class="metric-note">{{ note }}</span>
  </button>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  label: string
  value: string | number
  note: string
  trend?: string
  tone?: 'teal' | 'amber' | 'coral' | 'ink'
}>(), { tone: 'teal', trend: '' })
defineEmits<{ (event: 'click'): void }>()
</script>

<style scoped>
.signal-metric { min-width: 0; padding: 17px 18px 16px; text-align: left; color: var(--zh-text-primary); background: var(--zh-bg-card); border: 1px solid var(--zh-border-light); border-radius: var(--zh-radius); box-shadow: var(--zh-shadow); cursor: pointer; transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease; }
.signal-metric:hover { transform: translateY(-2px); box-shadow: var(--zh-shadow-hover); border-color: #bdd5cf; }
.metric-top { display: flex; justify-content: space-between; gap: 8px; }
.metric-label { color: var(--zh-text-regular); font-size: 12px; font-weight: 700; }
.metric-trend { color: var(--zh-primary); font-size: 11px; }
.metric-value { display: block; margin: 11px 0 5px; font-size: clamp(24px, 2.3vw, 34px); line-height: 1; letter-spacing: -.045em; }
.metric-note { display: block; overflow: hidden; color: var(--zh-text-secondary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.signal-metric.amber { border-top: 3px solid var(--zh-amber); }
.signal-metric.coral { border-top: 3px solid var(--zh-coral); }
.signal-metric.teal { border-top: 3px solid var(--zh-signal); }
.signal-metric.ink { border-top: 3px solid var(--zh-ink-soft); }
</style>
