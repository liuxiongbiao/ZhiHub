<!-- frontend/src/components/trace/RetrievalResult.vue -->
<!-- 职责: 检索结果展示 - 来源文档/置信度/纯向量vs混合对比 -->
<template>
  <div class="retrieval-result">
    <div class="result-header">
      <span class="result-label">{{ label }}</span>
      <span v-if="results.length === 0" class="no-result">无结果</span>
    </div>
    <div
      v-for="(item, idx) in results"
      :key="idx"
      class="result-card"
    >
      <div class="card-meta">
        <el-tag :type="sourceTagType(item.source)" size="small">
          {{ item.source }}
        </el-tag>
        <span class="card-filename">{{ item.doc_filename }}</span>
        <span class="card-score">相关度: {{ (item.score * 100).toFixed(1) }}%</span>
      </div>
      <div class="card-text">
        {{ item.chunk_text }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { ChunkResult } from '@/api/knowledge'

defineProps<{
  label: string
  results: ChunkResult[]
}>()

/** 检索来源 → el-tag 配色 */
function sourceTagType(source: string): 'success' | 'warning' | 'info' | 'danger' | '' {
  switch (source) {
    case 'hybrid':
    case 'hyde':
      return 'success'
    case 'vector':
      return 'info'
    case 'bm25':
      return 'warning'
    default:
      return ''
  }
}
</script>

<style scoped>
.retrieval-result {
  padding: 8px;
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #eee;
}

.result-label {
  font-weight: 600;
  font-size: 15px;
}

.no-result {
  color: #999;
  font-size: 13px;
}

.result-card {
  background: #f9f9f9;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 10px;
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  font-size: 13px;
}

.card-filename {
  color: #666;
}

.card-score {
  color: #409eff;
  font-weight: 500;
}

.card-text {
  font-size: 14px;
  line-height: 1.6;
  color: #333;
}
</style>
