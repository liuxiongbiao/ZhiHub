<template>
  <div class="service-rail" :class="{ compact }" aria-label="智枢服务闭环">
    <div
      v-for="(item, index) in items"
      :key="item.key"
      class="rail-node"
      :class="[{ active: active === item.key, done: activeIndex > index }, item.tone]"
    >
      <span class="node-index mono-data">0{{ index + 1 }}</span>
      <span class="node-copy">
        <strong>{{ item.label }}</strong>
        <small v-if="!compact">{{ item.detail }}</small>
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  active?: 'understand' | 'knowledge' | 'action' | 'handoff'
  compact?: boolean
}>(), { active: 'action', compact: false })

const items = [
  { key: 'understand', label: '理解诉求', detail: '识别意图与情绪', tone: 'teal' },
  { key: 'knowledge', label: '找到依据', detail: '检索企业可信知识', tone: 'teal' },
  { key: 'action', label: '完成业务', detail: '查询订单、创建工单', tone: 'teal' },
  { key: 'handoff', label: '人工兜底', detail: '风险场景无缝接管', tone: 'amber' },
] as const

const activeIndex = computed(() => items.findIndex((item) => item.key === props.active))
</script>

<style scoped>
.service-rail { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.rail-node { position: relative; min-width: 0; display: flex; gap: 10px; align-items: center; padding: 12px; color: #779096; background: rgba(255,255,255,.045); border: 1px solid rgba(255,255,255,.09); border-radius: 12px; }
.rail-node:not(:last-child)::after { content: ''; position: absolute; z-index: 2; right: -8px; top: 50%; width: 8px; border-top: 1px solid rgba(255,255,255,.2); }
.node-index { font-size: 10px; opacity: .65; }
.node-copy { display: grid; min-width: 0; }
.node-copy strong { color: inherit; font-size: 12px; white-space: nowrap; }
.node-copy small { margin-top: 2px; overflow: hidden; color: #779096; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.rail-node.done, .rail-node.active { color: #b9fff2; border-color: rgba(24,183,160,.5); background: rgba(24,183,160,.12); }
.rail-node.active { box-shadow: inset 0 0 0 1px rgba(24,183,160,.18), 0 0 18px rgba(24,183,160,.1); }
.rail-node.amber.active { color: #ffe2a6; border-color: rgba(230,162,60,.55); background: rgba(230,162,60,.13); }
.compact .rail-node { padding: 9px 10px; }
@media (max-width: 720px) { .service-rail { grid-template-columns: repeat(2, 1fr); } .rail-node::after { display: none; } }
</style>
