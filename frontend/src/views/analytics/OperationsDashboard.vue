<template>
  <main v-loading="loading" class="ops-page">
    <header class="ops-header">
      <div>
        <p class="eyebrow">ZHIHUB / OPERATIONS PULSE</p>
        <h1>运营脉搏</h1>
        <p class="subtitle">从检索命中到人工接管，再到回款，一屏判断服务是否顺畅。</p>
      </div>
      <div class="period-control">
        <span>观察窗口</span>
        <el-select v-model="days" aria-label="观察窗口" @change="loadOverview">
          <el-option :value="7" label="近 7 天" />
          <el-option :value="30" label="近 30 天" />
          <el-option :value="90" label="近 90 天" />
        </el-select>
      </div>
    </header>

    <el-alert
      v-if="overview && !overview.event_store_available"
      title="事件库暂时不可用，财务与套餐数据仍可查看；事件指标会在连接恢复后更新。"
      type="warning"
      :closable="false"
      show-icon
      class="store-alert"
    />

    <section v-if="overview" class="signal-board" aria-label="核心运营信号">
      <div class="signal-title">
        <span>服务闭环</span>
        <strong>{{ healthLabel }}</strong>
      </div>
      <div v-for="signal in signals" :key="signal.label" class="signal-item">
        <span class="signal-label">{{ signal.label }}</span>
        <strong>{{ signal.value }}</strong>
        <small>{{ signal.note }}</small>
      </div>
    </section>

    <template v-if="overview">
      <section class="analytics-grid">
        <article class="panel retrieval-panel">
          <div class="panel-heading">
            <div>
              <span class="section-code">RETRIEVAL</span>
              <h2>检索效果对照</h2>
            </div>
            <span class="panel-note">平均相关度 / 执行次数</span>
          </div>
          <div class="retrieval-list">
            <div v-for="item in overview.retrieval_comparison" :key="item.mode" class="retrieval-row">
              <div class="row-meta">
                <strong>{{ item.label }}</strong>
                <span>{{ item.executions }} 次 · {{ formatMs(item.avg_latency_ms) }}</span>
              </div>
              <div class="score-track" aria-hidden="true">
                <i :style="{ width: scoreWidth(item.avg_score) }" />
              </div>
              <b>{{ item.avg_score.toFixed(2) }}</b>
            </div>
          </div>
          <p v-if="!hasRetrieval" class="empty-hint">窗口内尚无检索事件。开始真实问答后会自动形成对照。</p>
        </article>

        <article class="panel resolution-panel">
          <div class="panel-heading">
            <div>
              <span class="section-code">RESOLUTION</span>
              <h2>自动解决走势</h2>
            </div>
            <span class="panel-note">最近 {{ resolutionPoints.length }} 天</span>
          </div>
          <div class="trend-chart" aria-label="自动与人工解决趋势">
            <div v-for="point in resolutionPoints" :key="point.date" class="trend-column">
              <div class="trend-bars">
                <i class="auto-bar" :style="{ height: trendHeight(point.auto) }" />
                <i class="human-bar" :style="{ height: trendHeight(point.human) }" />
              </div>
              <span>{{ shortDate(point.date) }}</span>
            </div>
          </div>
          <div class="legend"><i class="auto-dot" /> 自动解决 <i class="human-dot" /> 人工参与</div>
        </article>

        <article class="panel approval-panel">
          <div class="panel-heading">
            <div>
              <span class="section-code">APPROVAL</span>
              <h2>退款审批时效</h2>
            </div>
            <strong class="headline-number">{{ formatDuration(overview.summary.approval_latency_seconds) }}</strong>
          </div>
          <div class="bucket-list">
            <div v-for="bucket in overview.approval_latency_distribution" :key="bucket.label">
              <span>{{ bucket.label }}</span>
              <div><i :style="{ width: bucketWidth(bucket.count) }" /></div>
              <b>{{ bucket.count }}</b>
            </div>
          </div>
        </article>

        <article class="panel revenue-panel">
          <div class="panel-heading">
            <div>
              <span class="section-code">REVENUE</span>
              <h2>回款趋势</h2>
            </div>
            <strong class="headline-number">{{ formatMoney(totalRevenue) }}</strong>
          </div>
          <div v-if="overview.revenue_trend.length" class="revenue-bars">
            <div v-for="item in overview.revenue_trend" :key="item.period">
              <span>{{ item.period }}</span>
              <div><i :style="{ width: revenueWidth(item.revenue) }" /></div>
              <b>{{ formatMoney(item.revenue) }}</b>
            </div>
          </div>
          <p v-else class="empty-hint">当前窗口内没有已支付账单。</p>
          <div class="plan-strip">
            <span>套餐分布</span>
            <b v-for="item in overview.plan_distribution" :key="item.plan">
              {{ planLabel(item.plan) }} {{ item.count }}
            </b>
          </div>
        </article>
      </section>

      <section class="event-ledger">
        <div class="panel-heading">
          <div>
            <span class="section-code">EVENT LEDGER</span>
            <h2>最近业务事件</h2>
          </div>
          <span class="panel-note">共 {{ overview.summary.event_count }} 条</span>
        </div>
        <div v-if="overview.recent_events.length" class="event-list">
          <div v-for="event in overview.recent_events" :key="`${event.event_name}-${event.occurred_at}`" class="event-row">
            <time>{{ formatTime(event.occurred_at) }}</time>
            <strong>{{ eventLabel(event.event_name) }}</strong>
            <span>{{ eventDetail(event) }}</span>
          </div>
        </div>
        <p v-else class="empty-hint">尚无业务事件。埋点已启用，新操作会出现在这里。</p>
      </section>
    </template>

    <el-empty v-else-if="!loading" description="运营数据加载失败">
      <el-button type="primary" @click="loadOverview">重新加载</el-button>
    </el-empty>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getAnalyticsOverview, type AnalyticsOverview } from '@/api/analytics'

const days = ref(30)
const loading = ref(false)
const overview = ref<AnalyticsOverview | null>(null)

const signals = computed(() => {
  const summary = overview.value?.summary
  if (!summary) return []
  return [
    { label: '自动解决', value: `${summary.auto_resolution_rate.toFixed(1)}%`, note: '无需人工参与' },
    { label: '回答满意度', value: summary.average_csat ? summary.average_csat.toFixed(1) : '—', note: '满分 5 分' },
    { label: '工具成功率', value: `${summary.mcp_success_rate.toFixed(1)}%`, note: 'MCP 调用' },
    { label: '安全拦截', value: String(summary.security_blocks), note: '规则命中次数' },
  ]
})

const healthLabel = computed(() => {
  const summary = overview.value?.summary
  if (!summary || !overview.value?.event_store_available) return '等待信号'
  if (summary.security_blocks > 0 || (summary.mcp_success_rate > 0 && summary.mcp_success_rate < 90)) return '需要关注'
  return '运行平稳'
})
const hasRetrieval = computed(() => overview.value?.retrieval_comparison.some((item) => item.executions > 0))
const resolutionPoints = computed(() => overview.value?.resolution_trend.slice(-14) || [])
const maxTrend = computed(() => Math.max(1, ...resolutionPoints.value.flatMap((point) => [point.auto, point.human])))
const maxBucket = computed(() => Math.max(1, ...(overview.value?.approval_latency_distribution.map((item) => item.count) || [])))
const totalRevenue = computed(() => overview.value?.revenue_trend.reduce((sum, item) => sum + item.revenue, 0) || 0)
const maxRevenue = computed(() => Math.max(1, ...(overview.value?.revenue_trend.map((item) => item.revenue) || [])))

async function loadOverview() {
  loading.value = true
  try {
    const response = await getAnalyticsOverview(days.value)
    overview.value = response.data
  } catch (error: any) {
    overview.value = null
    ElMessage.error(error?.message || '运营数据加载失败')
  } finally {
    loading.value = false
  }
}

function scoreWidth(score: number) { return `${Math.max(2, Math.min(100, score * 100))}%` }
function trendHeight(value: number) { return `${Math.max(value ? 8 : 2, value / maxTrend.value * 100)}%` }
function bucketWidth(value: number) { return `${Math.max(value ? 6 : 0, value / maxBucket.value * 100)}%` }
function revenueWidth(value: number) { return `${Math.max(value ? 6 : 0, value / maxRevenue.value * 100)}%` }
function shortDate(value: string) { return value.slice(5).replace('-', '/') }
function formatMs(value: number) { return value ? `${Math.round(value)} ms` : '暂无耗时' }
function formatDuration(seconds: number) {
  if (!seconds) return '暂无数据'
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`
  return `${(seconds / 3600).toFixed(1)} 小时`
}
function formatMoney(value: number) { return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}` }
function formatTime(value: string) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
function planLabel(value: string) { return ({ trial: '试用', basic: '基础', pro: '专业', enterprise: '企业' } as Record<string, string>)[value] || value }
function eventLabel(value: string) {
  return ({
    session_created: '新建会话', retrieval_executed: '执行检索', mcp_tool_called: '调用工具',
    human_takeover: '人工接管', order_state_changed: '订单流转', refund_approved: '退款通过',
    refund_rejected: '退款驳回', quota_exceeded: '配额超限', invoice_paid: '账单支付',
    security_block: '安全拦截', answer_feedback: '回答反馈', index_rebuild_triggered: '重建索引',
    degradation_mode_entered: '进入降级', degradation_mode_exited: '退出降级', message_left: '用户留言',
    session_resolved: '会话解决',
  } as Record<string, string>)[value] || value
}
function eventDetail(event: AnalyticsOverview['recent_events'][number]) {
  const p = event.properties
  if (event.event_name === 'retrieval_executed') return `置信度 ${Number(p.confidence || 0).toFixed(2)} · ${p.latency || 0} ms`
  if (event.event_name === 'mcp_tool_called') return `${p.tool_name || '未知工具'} · ${p.success ? '成功' : '失败'}`
  if (event.event_name === 'order_state_changed') return `${p.from_status || '—'} → ${p.to_status || '—'}`
  if (event.event_name === 'session_resolved') return p.resolution_mode === 'human' ? '人工参与解决' : '自动解决'
  return Object.entries(p).slice(0, 2).map(([key, value]) => `${key}: ${String(value)}`).join(' · ') || '事件已记录'
}

onMounted(loadOverview)
</script>

<style scoped lang="scss">
.ops-page {
  --ink: #102a43;
  --teal: #0f8f8c;
  --coral: #e76f51;
  --mist: #eef5f5;
  --line: #d9e5e7;
  min-height: 100%;
  padding: 30px 34px 48px;
  color: var(--ink);
  background: #f5f8fa;
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
}
.ops-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; margin-bottom: 24px; }
.eyebrow, .section-code { color: var(--teal); font: 700 11px/1.2 "Bahnschrift", sans-serif; letter-spacing: .15em; }
h1 { margin: 7px 0 6px; font: 700 36px/1.08 "Microsoft YaHei", sans-serif; letter-spacing: -.04em; }
.subtitle { margin: 0; color: #5b7083; font-size: 14px; }
.period-control { display: flex; align-items: center; gap: 10px; color: #6a7f90; font-size: 13px; }
.period-control .el-select { width: 116px; }
.store-alert { margin-bottom: 18px; }
.signal-board {
  position: relative; display: grid; grid-template-columns: 1.15fr repeat(4, 1fr); overflow: hidden;
  border: 1px solid #183b52; border-radius: 14px; margin-bottom: 20px; color: white;
  background: var(--ink);
}
.signal-board::after { content: ""; position: absolute; inset: 0; pointer-events: none; opacity: .12; background: repeating-linear-gradient(115deg, transparent 0 42px, #fff 43px 44px); }
.signal-title, .signal-item { position: relative; z-index: 1; min-height: 112px; padding: 22px; border-right: 1px solid rgba(255,255,255,.14); }
.signal-title { display: flex; flex-direction: column; justify-content: space-between; background: rgba(15,143,140,.24); }
.signal-title span { color: #a9dcda; font-size: 12px; letter-spacing: .12em; }
.signal-title strong { font-size: 23px; }
.signal-item { display: flex; flex-direction: column; }
.signal-item strong { margin: 8px 0 3px; font: 700 27px/1 "Bahnschrift", sans-serif; }
.signal-label { color: #b9cad4; font-size: 12px; }
.signal-item small { color: #8ea6b5; }
.analytics-grid { display: grid; grid-template-columns: 1.2fr .8fr; gap: 20px; }
.panel, .event-ledger { border: 1px solid var(--line); border-radius: 14px; background: #fff; box-shadow: 0 8px 24px rgba(16,42,67,.045); }
.panel { min-height: 300px; padding: 24px; }
.panel-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.panel-heading h2 { margin: 5px 0 0; font-size: 20px; letter-spacing: -.025em; }
.panel-note { color: #8293a1; font-size: 12px; }
.headline-number { font: 700 18px/1 "Bahnschrift", sans-serif; color: var(--coral); }
.retrieval-list { margin-top: 27px; }
.retrieval-row { display: grid; grid-template-columns: 150px 1fr 42px; align-items: center; gap: 15px; margin: 20px 0; }
.row-meta { display: flex; flex-direction: column; gap: 3px; }
.row-meta strong { font-size: 14px; }
.row-meta span { color: #80909c; font-size: 11px; }
.score-track { height: 9px; overflow: hidden; border-radius: 9px; background: var(--mist); }
.score-track i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--teal), #66c7b9); transition: width .45s ease; }
.retrieval-row b { font: 700 15px "Bahnschrift", sans-serif; text-align: right; }
.trend-chart { display: flex; align-items: flex-end; gap: 8px; height: 190px; margin: 22px 0 8px; border-bottom: 1px solid var(--line); }
.trend-column { flex: 1; min-width: 8px; height: 100%; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; }
.trend-bars { display: flex; align-items: flex-end; gap: 2px; width: 100%; height: 155px; }
.trend-bars i { width: 50%; min-height: 2px; border-radius: 3px 3px 0 0; transition: height .45s ease; }
.auto-bar { background: var(--teal); } .human-bar { background: var(--coral); opacity: .82; }
.trend-column > span { color: #8999a5; font: 10px "Bahnschrift", sans-serif; transform: rotate(-45deg); margin: 9px 0 4px; }
.legend { display: flex; justify-content: flex-end; align-items: center; gap: 7px; color: #6c7e8d; font-size: 11px; }
.legend i { width: 7px; height: 7px; border-radius: 50%; margin-left: 8px; }
.auto-dot { background: var(--teal); } .human-dot { background: var(--coral); }
.bucket-list, .revenue-bars { margin-top: 25px; }
.bucket-list > div, .revenue-bars > div { display: grid; grid-template-columns: 90px 1fr 54px; gap: 12px; align-items: center; margin: 15px 0; font-size: 12px; }
.bucket-list > div > div, .revenue-bars > div > div { height: 8px; border-radius: 8px; background: var(--mist); overflow: hidden; }
.bucket-list i, .revenue-bars i { display: block; height: 100%; border-radius: inherit; background: var(--coral); }
.bucket-list b, .revenue-bars b { text-align: right; font-family: "Bahnschrift", sans-serif; }
.revenue-bars > div { grid-template-columns: 68px 1fr 82px; }
.revenue-bars i { background: var(--teal); }
.plan-strip { display: flex; flex-wrap: wrap; gap: 9px; align-items: center; margin-top: 26px; padding-top: 18px; border-top: 1px dashed var(--line); color: #718391; font-size: 12px; }
.plan-strip b { padding: 5px 9px; border-radius: 6px; color: var(--ink); background: var(--mist); }
.event-ledger { margin-top: 20px; padding: 24px; }
.event-list { margin-top: 18px; }
.event-row { display: grid; grid-template-columns: 120px 110px 1fr; align-items: center; min-height: 42px; border-top: 1px solid #edf2f3; font-size: 13px; }
.event-row time { color: #8798a4; font: 11px "Bahnschrift", sans-serif; }
.event-row strong { color: var(--teal); }
.event-row span { color: #5f7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty-hint { margin: 34px 0 0; color: #81919d; font-size: 13px; }
@media (max-width: 1000px) {
  .signal-board { grid-template-columns: repeat(2, 1fr); }
  .signal-title { grid-column: span 2; min-height: 86px; }
  .analytics-grid { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .ops-page { padding: 22px 16px 36px; }
  .ops-header { align-items: flex-start; flex-direction: column; }
  h1 { font-size: 31px; }
  .signal-board { grid-template-columns: 1fr 1fr; }
  .signal-item { min-height: 100px; padding: 17px; }
  .panel { padding: 19px; }
  .retrieval-row { grid-template-columns: 115px 1fr 38px; gap: 9px; }
  .event-row { grid-template-columns: 86px 88px 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  .score-track i, .trend-bars i { transition: none; }
}
</style>
