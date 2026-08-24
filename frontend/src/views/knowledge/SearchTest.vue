<template>
  <div class="search-page">
    <PageHeader title="检索测试台" subtitle="用客户真实说法验证：AI 能否找到正确依据">
      <template #actions><el-button @click="resetSearch">清空</el-button><el-button plain type="primary" @click="goQuality">返回质量中心</el-button></template>
    </PageHeader>

    <div class="page-container">
      <section class="test-hero surface-card">
        <div class="hero-copy"><p class="eyebrow">ANSWER EVIDENCE LAB</p><h1>问一个问题，看两种检索谁更靠谱</h1><p>左侧是纯语义匹配，右侧会结合关键词、语义和精排。重点看是否命中正确文档，而不只是分数高低。</p></div>
        <div class="search-box">
          <el-input v-model="query" placeholder="例如：拆封后还能在七天内退货吗？" size="large" clearable aria-label="要验证的客户问题" @keyup.enter="handleSearch">
            <template #append><el-button type="primary" :loading="searching" @click="handleSearch">验证回答依据</el-button></template>
          </el-input>
          <div class="example-queries"><span>试一试</span><button v-for="item in suggestions" :key="item" type="button" @click="runSuggestion(item)">{{ item }}</button></div>
        </div>
      </section>

      <section v-if="result && !result.empty_knowledge" class="result-summary surface-card" aria-live="polite">
        <div class="verdict-icon" :class="verdictTone">{{ verdictIcon }}</div>
        <div class="verdict-copy"><p class="eyebrow">VALIDATION RESULT</p><h2>{{ verdictTitle }}</h2><p>{{ verdictDetail }}</p></div>
        <div class="score-delta"><span>最佳依据提升</span><strong class="mono-data">{{ gainLabel }}</strong><small>混合检索相对纯向量</small></div>
      </section>

      <section v-if="result && !result.empty_knowledge" class="metric-grid">
        <SignalMetric label="纯向量最佳匹配" :value="percent(bestVector)" note="只看语义相似度" tone="ink" />
        <SignalMetric label="混合检索最佳匹配" :value="percent(bestHybrid)" note="关键词 + 语义 + 精排" :tone="bestHybrid >= bestVector ? 'teal' : 'amber'" />
        <SignalMetric label="完整检索耗时" :value="`${result.elapsed_ms}ms`" note="不包含生成回答" :tone="result.elapsed_ms > 1500 ? 'amber' : 'teal'" />
        <SignalMetric label="命中文档" :value="uniqueDocuments" note="查看是否覆盖正确政策" tone="ink" />
      </section>

      <section v-if="result && !result.empty_knowledge" class="comparison-grid">
        <article class="result-panel surface-card">
          <header><div><span class="method-code">A / VECTOR</span><h2>纯向量召回</h2><p>适合语义近似，但可能错过订单号、产品名和政策关键词。</p></div><strong class="mono-data">{{ percent(bestVector) }}</strong></header>
          <RetrievalResult label="纯向量召回 (Top5)" :results="result.vector_results" />
        </article>
        <article class="result-panel recommended surface-card">
          <header><div><span class="method-code">B / HYBRID + RERANK</span><h2>混合检索</h2><p>把关键词与语义结果合并，再从少量候选中精排。</p></div><strong class="mono-data">{{ percent(bestHybrid) }}</strong></header>
          <div class="recommended-chip">实际回答采用</div>
          <RetrievalResult label="混合检索 (RRF + Rerank Top5)" :results="result.hybrid_results" />
        </article>
      </section>

      <details class="expert-panel surface-card">
        <summary><span><strong>专家设置与性能明细</strong><small>查询改写、对话历史、HyDE 与分阶段耗时</small></span><b>展开</b></summary>
        <div class="expert-body">
          <div class="rewrite-info">
            <span>原始问题</span><code>{{ result?.query || query || '尚未运行' }}</code>
            <span>改写后</span><code>{{ result?.rewritten_query || '未改写' }}</code>
            <span>兜底策略</span><strong>{{ result?.hyde_fallback ? '已启用 HyDE 假设答案' : '未触发' }}</strong>
          </div>
          <div class="timing-list" v-if="result && Object.keys(result.timings_ms || {}).length">
            <div v-for="(value, key) in result.timings_ms" :key="key"><span>{{ timingLabel(key) }}</span><i :style="{ width: timingWidth(Number(value)) }" /><strong class="mono-data">{{ value }}ms</strong></div>
          </div>
          <div class="history-editor">
            <header><div><strong>模拟对话上下文</strong><p>用于验证“它怎么办”“刚才那个订单”等追问能否被正确改写。</p></div><el-button link type="primary" @click="addHistoryTurn">添加一轮</el-button></header>
            <div v-for="(turn, index) in history" :key="index" class="history-turn"><el-input v-model="turn.user" placeholder="客户上一句" /><el-input v-model="turn.assistant" placeholder="AI 上一条回复" /><el-button link type="danger" @click="removeHistoryTurn(index)">删除</el-button></div>
            <p v-if="!history.length" class="history-empty">当前按独立问题验证；需要测试追问时再添加历史。</p>
          </div>
        </div>
      </details>

      <section v-if="result?.empty_knowledge" class="empty-knowledge surface-card"><span>∅</span><div><h2>还没有可检索的知识</h2><p>先上传并发布企业政策、产品说明或服务流程，再回到这里验证客户问题。</p><el-button type="primary" @click="goDocuments">去管理知识文档</el-button></div></section>
      <section v-else-if="!result && !searching" class="preflight surface-card"><div class="preflight-mark">?</div><div><h2>先从一个高频客户问题开始</h2><p>建议优先验证退款、物流、套餐、发票等会影响真实业务处理的问题。</p></div><ol><li><span>1</span>输入客户原话</li><li><span>2</span>确认命中文档</li><li><span>3</span>比较检索提升</li></ol></section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { searchTest, type SearchTestResponse } from '@/api/knowledge'
import PageHeader from '@/components/common/PageHeader.vue'
import SignalMetric from '@/components/common/SignalMetric.vue'
import RetrievalResult from '@/components/trace/RetrievalResult.vue'

const suggestions = ['拆封后还能七天退货吗？', '订单发货后怎么查物流？', '企业版支持多少坐席？']
const query = ref('')
const searching = ref(false)
const result = ref<SearchTestResponse | null>(null)
const history = ref<Array<{ user: string; assistant: string }>>([])
const bestVector = computed(() => Math.max(0, ...(result.value?.vector_results.map((item) => item.score) || [])))
const bestHybrid = computed(() => Math.max(0, ...(result.value?.hybrid_results.map((item) => item.score) || [])))
const gain = computed(() => bestHybrid.value - bestVector.value)
const gainLabel = computed(() => `${gain.value >= 0 ? '+' : ''}${Math.round(gain.value * 100)} 个百分点`)
const uniqueDocuments = computed(() => new Set((result.value?.hybrid_results || []).map((item) => item.doc_id)).size)
const verdictTone = computed(() => bestHybrid.value >= .75 ? 'good' : bestHybrid.value >= .5 ? 'watch' : 'risk')
const verdictIcon = computed(() => bestHybrid.value >= .75 ? '✓' : bestHybrid.value >= .5 ? '~' : '!')
const verdictTitle = computed(() => bestHybrid.value >= .75 ? '已找到较明确的回答依据' : bestHybrid.value >= .5 ? '能找到相关内容，但建议人工抽查' : '依据不够明确，建议补充知识')
const verdictDetail = computed(() => {
  if (!result.value) return ''
  if (bestHybrid.value > bestVector.value + .03) return `混合检索比纯向量提高了 ${Math.round(gain.value * 100)} 个百分点，优先检查右侧第一条是否确实回答了客户问题。`
  if (bestHybrid.value >= .65) return '两种检索结果接近。重点确认第一条来源是否正确，以及文字是否覆盖客户的具体问法。'
  return '当前命中分数偏低。可以补充客户常用说法、更新文档标题，或增加更直接的政策说明。'
})
const maxTiming = computed(() => Math.max(1, ...Object.values(result.value?.timings_ms || {}).map(Number)))

function percent(value: number) { return `${Math.round(value * 100)}%` }
function timingWidth(value: number) { return `${Math.max(4, value / maxTiming.value * 100)}%` }
function timingLabel(key: string) { return ({ rewrite: '查询改写', embedding: '向量编码', vector: '向量召回', keyword: '关键词召回', rerank: '候选精排', total: '完整检索' } as Record<string, string>)[key] || key }
function addHistoryTurn() { history.value.push({ user: '', assistant: '' }) }
function removeHistoryTurn(index: number) { history.value.splice(index, 1) }
function runSuggestion(value: string) { query.value = value; handleSearch() }
function resetSearch() { query.value = ''; result.value = null; history.value = []; window.history.replaceState({}, '', window.location.pathname) }
function goQuality() { window.location.assign('/knowledge/quality') }
function goDocuments() { window.location.assign('/knowledge/list') }

async function handleSearch() {
  if (!query.value.trim()) { ElMessage.warning('请输入要验证的客户问题'); return }
  searching.value = true
  try {
    const messages = history.value.filter((item) => item.user.trim() || item.assistant.trim()).flatMap((item) => [{ role: 'user', content: item.user }, { role: 'assistant', content: item.assistant }])
    result.value = (await searchTest(query.value.trim(), messages)).data
  } catch (error: any) { ElMessage.error(error?.message || '检索验证失败') }
  finally { searching.value = false }
}
onMounted(() => {
  const preset = new URLSearchParams(window.location.search).get('q')
  if (preset) { query.value = preset; handleSearch() }
})
</script>

<style scoped>
.test-hero { display: grid; grid-template-columns: minmax(300px,.75fr) minmax(460px,1.25fr); gap: 38px; align-items: center; padding: 28px; color: #fff; background: var(--zh-ink); }.hero-copy h1 { margin: 7px 0 9px; font-size: clamp(23px,2.7vw,34px); letter-spacing: -.04em; }.hero-copy > p:last-child { color: #a6b8ba; font-size: 13px; line-height: 1.7; }.search-box { min-width: 0; }.search-box :deep(.el-input__wrapper) { min-height: 52px; }.example-queries { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; margin-top: 11px; }.example-queries span { color: #82999c; font-size: 11px; }.example-queries button { padding: 6px 9px; color: #c0d0d1; font-size: 11px; background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.12); border-radius: 99px; cursor: pointer; }.example-queries button:hover { color: #fff; border-color: var(--zh-signal); }
.result-summary { display: grid; grid-template-columns: auto 1fr auto; gap: 17px; align-items: center; margin-top: 14px; padding: 20px; }.verdict-icon { display: grid; place-items: center; width: 45px; height: 45px; color: #fff; font-size: 20px; font-weight: 800; background: var(--zh-primary); border-radius: 14px; }.verdict-icon.watch { background: var(--zh-amber); }.verdict-icon.risk { background: var(--zh-coral); }.verdict-copy h2 { margin: 4px 0; font-size: 18px; }.verdict-copy > p:last-child { color: var(--zh-text-regular); font-size: 12px; line-height: 1.55; }.score-delta { display: grid; padding-left: 22px; border-left: 1px solid var(--zh-border-light); text-align: right; }.score-delta span, .score-delta small { color: var(--zh-text-secondary); font-size: 10px; }.score-delta strong { margin: 4px 0; color: var(--zh-primary); font-size: 19px; }
.metric-grid { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 12px; margin-top: 14px; }.comparison-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 14px; margin-top: 14px; }.result-panel { position: relative; min-width: 0; padding: 21px; }.result-panel.recommended { border-color: #9ecfc7; box-shadow: 0 0 0 2px var(--zh-primary-bg); }.result-panel header { display: flex; justify-content: space-between; gap: 15px; margin-bottom: 16px; padding-bottom: 15px; border-bottom: 1px solid var(--zh-border-light); }.result-panel h2 { margin: 4px 0; font-size: 19px; }.result-panel header p { color: var(--zh-text-secondary); font-size: 11px; line-height: 1.5; }.result-panel header > strong { color: var(--zh-primary); font-size: 24px; }.method-code { color: var(--zh-primary); font-size: 10px; font-weight: 800; letter-spacing: .1em; }.recommended-chip { position: absolute; top: -8px; right: 18px; padding: 5px 9px; color: #fff; font-size: 10px; font-weight: 700; background: var(--zh-primary); border-radius: 6px; }
.expert-panel { margin-top: 14px; padding: 0 21px; }.expert-panel summary { display: flex; justify-content: space-between; align-items: center; padding: 17px 0; cursor: pointer; list-style: none; }.expert-panel summary span { display: grid; }.expert-panel summary strong { font-size: 13px; }.expert-panel summary small { margin-top: 3px; color: var(--zh-text-secondary); font-size: 11px; }.expert-panel summary b { color: var(--zh-primary); font-size: 11px; }.expert-panel[open] summary b { font-size: 0; }.expert-panel[open] summary b::after { font-size: 11px; content: '收起'; }.expert-body { display: grid; grid-template-columns: .9fr 1.1fr; gap: 22px; padding: 0 0 22px; border-top: 1px solid var(--zh-border-light); }.rewrite-info { display: grid; grid-template-columns: 72px 1fr; gap: 9px; padding-top: 17px; align-content: start; font-size: 11px; }.rewrite-info span { color: var(--zh-text-secondary); }.rewrite-info code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.timing-list { grid-column: 2; grid-row: 1; padding-top: 12px; }.timing-list div { display: grid; grid-template-columns: 70px 1fr 54px; gap: 9px; align-items: center; margin-top: 8px; font-size: 10px; }.timing-list i { height: 6px; background: var(--zh-primary); border-radius: 99px; }.timing-list strong { text-align: right; }.history-editor { grid-column: 1/-1; padding-top: 16px; border-top: 1px dashed var(--zh-border); }.history-editor header { display: flex; justify-content: space-between; }.history-editor header strong { font-size: 12px; }.history-editor header p, .history-empty { margin-top: 3px; color: var(--zh-text-secondary); font-size: 10px; }.history-turn { display: grid; grid-template-columns: 1fr 1fr auto; gap: 9px; margin-top: 10px; }
.empty-knowledge, .preflight { display: grid; align-items: center; margin-top: 14px; padding: 30px; }.empty-knowledge { grid-template-columns: auto 1fr; gap: 20px; }.empty-knowledge > span, .preflight-mark { display: grid; place-items: center; width: 58px; height: 58px; color: var(--zh-primary); font-size: 25px; font-weight: 800; background: var(--zh-primary-bg); border-radius: 18px; }.empty-knowledge h2, .preflight h2 { margin-bottom: 6px; font-size: 19px; }.empty-knowledge p, .preflight p { margin-bottom: 14px; color: var(--zh-text-secondary); font-size: 12px; }.preflight { grid-template-columns: auto 1fr auto; gap: 19px; }.preflight p { margin: 0; }.preflight ol { display: flex; gap: 13px; list-style: none; }.preflight li { display: grid; justify-items: center; gap: 5px; color: var(--zh-text-secondary); font-size: 10px; }.preflight li span { display: grid; place-items: center; width: 26px; height: 26px; color: #fff; background: var(--zh-ink-soft); border-radius: 50%; }
@media (max-width: 950px) { .test-hero { grid-template-columns: 1fr; }.metric-grid { grid-template-columns: repeat(2,1fr); }.comparison-grid { grid-template-columns: 1fr; }.preflight { grid-template-columns: auto 1fr; }.preflight ol { grid-column: 1/-1; } }
@media (max-width: 600px) { .test-hero { padding: 20px; }.metric-grid { grid-template-columns: 1fr; }.result-summary { grid-template-columns: auto 1fr; }.score-delta { grid-column: 1/-1; padding: 12px 0 0; border-top: 1px solid var(--zh-border-light); border-left: 0; text-align: left; }.expert-body { grid-template-columns: 1fr; }.timing-list { grid-column: 1; grid-row: auto; }.history-turn { grid-template-columns: 1fr; }.preflight { grid-template-columns: 1fr; }.preflight ol { flex-wrap: wrap; } }
</style>
