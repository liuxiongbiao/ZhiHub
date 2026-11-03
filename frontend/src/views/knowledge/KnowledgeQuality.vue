<template>
  <div class="quality-page">
    <PageHeader title="知识质量中心" subtitle="把文档状态和真实客户问题放在一起看，知道下一步该补什么">
      <template #actions><el-select v-model="days" style="width: 110px" @change="load"><el-option label="近 7 天" :value="7" /><el-option label="近 30 天" :value="30" /><el-option label="近 90 天" :value="90" /></el-select><el-button @click="load">刷新</el-button></template>
    </PageHeader>

    <div class="page-container" v-loading="loading">
      <el-alert v-if="data && !data.event_store_available" type="warning" :closable="false" show-icon title="检索事件库暂不可用" description="文档健康仍可查看；低置信问题和延迟指标会在事件库恢复后补齐。" />

      <section class="quality-hero surface-card">
        <div class="score-ring" :style="scoreStyle"><strong class="mono-data">{{ data?.quality_score || 0 }}</strong><span>知识健康分</span></div>
        <div class="quality-copy"><p class="eyebrow">知识状况</p><h1>{{ qualityHeadline }}</h1><p>{{ qualityExplanation }}</p><div class="quality-actions"><el-button type="primary" @click="router.push('/knowledge/list')">管理知识文档</el-button><el-button plain @click="router.push('/knowledge/search-test')">运行回答验证</el-button></div></div>
        <div class="quality-legend"><div><i class="ready"></i><span>可直接用于回答</span><strong>{{ docs.ready }}</strong></div><div><i class="review"></i><span>需要人工处理</span><strong>{{ docs.failed + docs.pending_review + docs.expired }}</strong></div><div><i class="process"></i><span>正在处理中</span><strong>{{ docs.processing }}</strong></div></div>
      </section>

      <section class="metric-grid">
        <SignalMetric label="已就绪文档" :value="`${docs.ready}/${docs.total}`" :note="`${docs.chunks} 个可检索知识切片`" tone="teal" @click="router.push('/knowledge/list')" />
        <SignalMetric label="平均匹配信心" :value="formatPercent(data?.retrieval.average_confidence)" note="越高表示答案依据越明确" tone="ink" @click="router.push('/knowledge/search-test')" />
        <SignalMetric label="检索 P95" :value="`${Math.round(data?.retrieval.p95_latency_ms || 0)}ms`" note="95% 的检索请求不超过此耗时" :tone="latencyTone" />
        <SignalMetric label="低置信问题" :value="data?.retrieval.low_confidence_count || 0" note="建议补充说法或更新内容" :tone="(data?.retrieval.low_confidence_count || 0) > 0 ? 'coral' : 'teal'" />
      </section>

      <section class="quality-grid">
        <div class="surface-card recommendation-card">
          <div class="card-head"><div><p class="eyebrow">下一步</p><h2>建议先做这些</h2></div></div>
          <button v-for="(item, index) in data?.recommendations || []" :key="item.title" type="button" class="recommendation" @click="router.push(item.path)"><span class="mono-data">0{{ index + 1 }}</span><div><strong>{{ item.title }}</strong><small>{{ recommendationDetail(item.title) }}</small></div><el-tag :type="item.severity === 'high' ? 'danger' : item.severity === 'medium' ? 'warning' : 'success'" size="small" effect="light">{{ severityLabel(item.severity) }}</el-tag><b>→</b></button>
        </div>

        <div class="surface-card coverage-card">
          <div class="card-head"><div><p class="eyebrow">文档覆盖</p><h2>文档准备度</h2></div><strong class="mono-data">{{ readiness }}%</strong></div>
          <div class="coverage-track"><i class="ready" :style="{ width: `${readyWidth}%` }"></i><i class="process" :style="{ width: `${processWidth}%` }"></i><i class="risk" :style="{ width: `${riskWidth}%` }"></i></div>
          <dl class="coverage-list"><div><dt><i class="ready"></i> 已就绪</dt><dd>{{ docs.ready }}</dd></div><div><dt><i class="process"></i> 处理中</dt><dd>{{ docs.processing }}</dd></div><div><dt><i class="risk"></i> 失败 / 待复核 / 过期</dt><dd>{{ docs.failed + docs.pending_review + docs.expired }}</dd></div></dl>
          <el-collapse class="expert-box"><el-collapse-item title="专家指标说明"><p>健康分由文档就绪度（55%）与真实检索置信度（45%）组成。P95 是检索耗时的第 95 百分位，不包含回答生成时间。</p></el-collapse-item></el-collapse>
        </div>
      </section>

      <section class="surface-card missed-card">
        <div class="card-head"><div><p class="eyebrow">客户原话</p><h2>系统没把握的问题</h2><p>这些是真实检索中置信度低于阈值的问题，可直接带到回答验证台复现。</p></div><el-button link type="primary" @click="router.push('/knowledge/search-test')">打开验证台</el-button></div>
        <div v-if="data?.missed_queries.length" class="missed-list"><button v-for="item in data.missed_queries" :key="item.query + item.occurred_at" type="button" @click="retest(item.query)"><span class="question-mark">?</span><span><strong>{{ item.query }}</strong><small>{{ formatTime(item.occurred_at) }}</small></span><b class="mono-data">{{ formatPercent(item.confidence) }}</b><span>重新验证 →</span></button></div>
        <div v-else class="empty-action"><div><strong>暂无低置信问题</strong><p>继续使用示例问题抽查，保持知识质量稳定。</p><el-button type="primary" plain @click="router.push('/knowledge/search-test')">开始验证</el-button></div></div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getKnowledgeQuality, type KnowledgeQualityOverview } from '@/api/analytics'
import PageHeader from '@/components/common/PageHeader.vue'
import SignalMetric from '@/components/common/SignalMetric.vue'

const router = useRouter()
const days = ref(30)
const loading = ref(false)
const data = ref<KnowledgeQualityOverview | null>(null)
const emptyDocs = { total: 0, ready: 0, processing: 0, failed: 0, pending_review: 0, expired: 0, chunks: 0 }
const docs = computed(() => data.value?.documents || emptyDocs)
const readiness = computed(() => docs.value.total ? Math.round(docs.value.ready / docs.value.total * 100) : 0)
const readyWidth = computed(() => docs.value.total ? docs.value.ready / docs.value.total * 100 : 0)
const processWidth = computed(() => docs.value.total ? docs.value.processing / docs.value.total * 100 : 0)
const riskWidth = computed(() => Math.max(0, 100 - readyWidth.value - processWidth.value))
const qualityHeadline = computed(() => (data.value?.quality_score || 0) >= 85 ? '知识库已具备稳定回答能力' : (data.value?.quality_score || 0) >= 60 ? '基础可用，还有提升空间' : '先补齐知识，再扩大自动回答')
const qualityExplanation = computed(() => docs.value.total ? `当前 ${docs.value.ready} 份文档可直接回答，近 ${days.value} 天发生 ${data.value?.retrieval.executions || 0} 次知识检索。` : '还没有可用于回答的业务文档，先上传政策、产品或服务流程。')
const scoreStyle = computed(() => ({ '--score': `${Math.max(0, Math.min(100, data.value?.quality_score || 0)) * 3.6}deg` }))
const latencyTone = computed(() => (data.value?.retrieval.p95_latency_ms || 0) > 1500 ? 'amber' : 'teal')
function formatPercent(value?: number) { return `${Math.round((value || 0) * 100)}%` }
function formatTime(value: string) { return value ? new Date(value).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '近期' }
function severityLabel(value: string) { return ({ high: '优先处理', medium: '建议优化', low: '持续验证' } as Record<string, string>)[value] || value }
function recommendationDetail(title: string) { if (title.includes('上传')) return '先提供可信内容，AI 才能依据企业口径回答。'; if (title.includes('失败')) return '恢复文档解析与发布状态，避免答案缺口。'; if (title.includes('同义')) return '把客户的口语表达加入知识或示例问题。'; return '用高频问题持续抽查答案与引用。' }
function retest(query: string) { router.push({ path: '/knowledge/search-test', query: { q: query } }) }
async function load() { loading.value = true; try { data.value = (await getKnowledgeQuality(days.value)).data } catch (error: any) { ElMessage.error(error?.message || '加载知识质量失败') } finally { loading.value = false } }
onMounted(load)
</script>

<style scoped>
.quality-hero { display: grid; grid-template-columns: auto 1fr minmax(230px,.55fr); gap: 28px; align-items: center; padding: 28px; }.score-ring { display: grid; place-items: center; width: 150px; height: 150px; background: radial-gradient(circle, var(--zh-bg-card) 61%, transparent 62%), conic-gradient(var(--zh-primary) var(--score), #e3eae7 0); border-radius: 50%; }.score-ring strong { font-size: 39px; line-height: 1; }.score-ring span { margin-top: -40px; color: var(--zh-text-secondary); font-size: 9px; }.quality-copy h1 { margin: 7px 0 9px; font-size: clamp(22px,2.4vw,33px); letter-spacing: -.035em; }.quality-copy > p:not(.eyebrow) { color: var(--zh-text-regular); line-height: 1.7; }.quality-actions { display: flex; gap: 8px; margin-top: 18px; }.quality-legend { padding: 4px 0 4px 24px; border-left: 1px solid var(--zh-border-light); }.quality-legend div { display: grid; grid-template-columns: auto 1fr auto; gap: 9px; align-items: center; padding: 9px 0; font-size: 10px; }.quality-legend i, .coverage-list i { width: 8px; height: 8px; background: var(--zh-signal); border-radius: 3px; }.quality-legend i.review { background: var(--zh-coral); }.quality-legend i.process { background: var(--zh-amber); }.quality-legend strong { font-size: 15px; }
.metric-grid { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 13px; margin-top: 14px; }.quality-grid { display: grid; grid-template-columns: minmax(0,1.2fr) minmax(330px,.8fr); gap: 14px; margin-top: 14px; }.recommendation-card, .coverage-card, .missed-card { padding: 21px; }.card-head { display: flex; justify-content: space-between; align-items: flex-start; }.card-head h2 { margin-top: 4px; font-size: 18px; }.card-head > div > p:last-child:not(.eyebrow) { margin-top: 4px; color: var(--zh-text-secondary); font-size: 10px; }.recommendation { display: grid; grid-template-columns: auto 1fr auto auto; gap: 11px; align-items: center; width: 100%; padding: 13px 4px; text-align: left; color: var(--zh-text-primary); background: transparent; border: 0; border-top: 1px solid var(--zh-border-light); cursor: pointer; }.recommendation:first-of-type { margin-top: 14px; }.recommendation > span:first-child { color: var(--zh-text-placeholder); font-size: 9px; }.recommendation div { display: grid; }.recommendation strong { font-size: 11px; }.recommendation small { margin-top: 3px; color: var(--zh-text-secondary); font-size: 9px; }.recommendation b { color: var(--zh-text-placeholder); }.coverage-card .card-head > strong { color: var(--zh-primary); font-size: 25px; }.coverage-track { display: flex; height: 9px; overflow: hidden; margin: 23px 0 17px; background: #edf1ef; border-radius: 99px; }.coverage-track i { height: 100%; }.coverage-track .ready { background: var(--zh-signal); }.coverage-track .process { background: var(--zh-amber); }.coverage-track .risk { background: var(--zh-coral); }.coverage-list div { display: flex; justify-content: space-between; padding: 8px 0; font-size: 10px; }.coverage-list dt { display: flex; gap: 8px; align-items: center; color: var(--zh-text-regular); }.coverage-list i.process { background: var(--zh-amber); }.coverage-list i.risk { background: var(--zh-coral); }.expert-box { margin-top: 15px; }.expert-box p { color: var(--zh-text-secondary); font-size: 9px; line-height: 1.7; }
.missed-card { margin-top: 14px; }.missed-list { margin-top: 14px; }.missed-list button { display: grid; grid-template-columns: auto 1fr auto auto; gap: 12px; align-items: center; width: 100%; padding: 13px 6px; text-align: left; color: var(--zh-text-primary); background: transparent; border: 0; border-top: 1px solid var(--zh-border-light); cursor: pointer; }.missed-list button:hover { background: #f5faf7; }.question-mark { display: grid; place-items: center; width: 27px; height: 27px; color: #fff; font-weight: 800; background: var(--zh-coral); border-radius: 9px; }.missed-list button > span:nth-child(2) { display: grid; }.missed-list strong { font-size: 11px; }.missed-list small { margin-top: 3px; color: var(--zh-text-secondary); font-size: 9px; }.missed-list b { color: var(--zh-coral); }.missed-list button > span:last-child { color: var(--zh-primary); font-size: 9px; }.empty-action strong { display: block; }.empty-action p { margin: 6px 0 14px; font-size: 10px; }
@media (max-width: 1050px) { .quality-hero { grid-template-columns: auto 1fr; }.quality-legend { grid-column: 1/-1; display: grid; grid-template-columns: repeat(3,1fr); padding: 12px 0 0; border-left: 0; border-top: 1px solid var(--zh-border-light); }.metric-grid { grid-template-columns: repeat(2,1fr); }.quality-grid { grid-template-columns: 1fr; } }
@media (max-width: 650px) { .quality-hero { grid-template-columns: 1fr; }.score-ring { margin: auto; }.quality-legend, .metric-grid { grid-template-columns: 1fr; }.missed-list button { grid-template-columns: auto 1fr auto; }.missed-list button > span:last-child { display: none; } }
</style>
