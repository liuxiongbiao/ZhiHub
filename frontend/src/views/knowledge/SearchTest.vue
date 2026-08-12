<!-- frontend/src/views/knowledge/SearchTest.vue -->
<!-- 职责: 检索测试台 - 纯向量 vs 混合检索结果对比展示 -->
<template>
  <div class="search-test">
    <div class="search-header">
      <h2>检索测试台</h2>
      <p class="hint">输入查询，对比纯向量召回与混合检索的结果差异</p>
    </div>

    <div class="search-input-area">
      <el-input
        v-model="query"
        placeholder="输入查询问题，如：如何升级套餐 / 退款政策"
        size="large"
        clearable
        @keyup.enter="handleSearch"
      >
        <template #append>
          <el-button
            type="primary"
            :loading="searching"
            @click="handleSearch"
          >
            检索对比
          </el-button>
        </template>
      </el-input>
    </div>

    <!-- 历史对话（可选） -->
    <div class="history-area" v-if="showHistory">
      <div class="history-header">
        <span>对话历史（用于测试查询改写）</span>
        <el-button type="text" size="small" @click="addHistoryTurn">+ 添加一轮</el-button>
      </div>
      <div v-for="(turn, idx) in history" :key="idx" class="history-turn">
        <el-input
          v-model="turn.user"
          placeholder="用户消息"
          size="small"
          style="margin-bottom: 4px"
        />
        <el-input
          v-model="turn.assistant"
          placeholder="助手回复"
          size="small"
        />
        <el-button type="text" size="small" @click="removeHistoryTurn(idx)">删除</el-button>
      </div>
    </div>

    <el-button type="text" @click="showHistory = !showHistory" style="margin-bottom: 16px">
      {{ showHistory ? '隐藏' : '显示' }}对话历史
    </el-button>

    <!-- 检索结果 -->
    <div v-if="result && !result.empty_knowledge" class="results-area">
      <div class="result-meta">
        <span v-if="result.rewritten_query">
          改写查询: <code>{{ result.rewritten_query }}</code>
        </span>
        <span v-if="result.hyde_fallback" class="hyde-badge">
          <el-tag type="warning">HyDE 兜底</el-tag>
        </span>
        <span class="elapsed">总耗时: {{ result.elapsed_ms }}ms</span>
      </div>

      <el-row :gutter="20">
        <el-col :span="12">
          <RetrievalResult
            label="纯向量召回 (Top5)"
            :results="result.vector_results"
          />
        </el-col>
        <el-col :span="12">
          <RetrievalResult
            label="混合检索 (RRF + Rerank Top5)"
            :results="result.hybrid_results"
          />
        </el-col>
      </el-row>
    </div>

    <!-- 空知识库 -->
    <el-empty
      v-if="result?.empty_knowledge"
      description="知识库为空，请先上传文档"
    />
    <el-empty
      v-if="!result && !searching && searched"
      description="输入查询并点击「检索对比」"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { searchTest, type SearchTestResponse } from '@/api/knowledge'
import RetrievalResult from '@/components/trace/RetrievalResult.vue'

const query = ref('')
const searching = ref(false)
const searched = ref(false)
const result = ref<SearchTestResponse | null>(null)
const showHistory = ref(false)
const history = ref<Array<{ user: string; assistant: string }>>([])

/** 添加一轮空的历史对话（用于测试查询改写） */
function addHistoryTurn() {
  history.value.push({ user: '', assistant: '' })
}

/** 删除指定轮历史对话 */
function removeHistoryTurn(idx: number) {
  history.value.splice(idx, 1)
}

/** 执行检索对比：调 searchTest 并展示纯向量 vs 混合检索结果 */
async function handleSearch() {
  if (!query.value.trim()) {
    ElMessage.warning('请输入查询问题')
    return
  }

  searching.value = true
  searched.value = true
  try {
    const historyMessages = history.value
      .filter(h => h.user.trim() || h.assistant.trim())
      .flatMap(h => [
        { role: 'user', content: h.user },
        { role: 'assistant', content: h.assistant },
      ])

    const res = await searchTest(query.value.trim(), historyMessages)
    result.value = res.data
  } catch (e: any) {
    ElMessage.error(e?.message || '检索失败')
  } finally {
    searching.value = false
  }
}
</script>

<style scoped>
.search-test {
  padding: 20px;
  max-width: 1200px;
}

.search-header {
  margin-bottom: 20px;
}

.search-header h2 {
  margin-bottom: 4px;
}

.hint {
  color: #999;
  font-size: 13px;
}

.search-input-area {
  margin-bottom: 16px;
}

.history-area {
  background: #f5f5f5;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 16px;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.history-turn {
  margin-bottom: 8px;
  padding: 8px;
  background: white;
  border-radius: 4px;
}

.results-area {
  margin-top: 16px;
}

.result-meta {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
  padding: 8px 12px;
  background: #f0f9ff;
  border-radius: 6px;
  font-size: 13px;
}

.hyde-badge {
  display: inline-flex;
  align-items: center;
}

.elapsed {
  color: #999;
  margin-left: auto;
}
</style>
