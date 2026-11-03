<template>
  <div class="home-page">
    <section class="command-hero">
      <div class="hero-copy">
        <p class="eyebrow">SERVICE COMMAND · {{ todayLabel }}</p>
        <h1>{{ greeting }}，{{ tenantStore.tenant_name || '欢迎回来' }}</h1>
        <p>先看需要行动的信号，再进入客户服务现场。智枢会把知识回答、业务办理和人工接管串成一个闭环。</p>
        <div class="hero-actions">
          <el-button v-if="isAdmin" type="primary" size="large" @click="router.push('/demo-center')">开始 3 分钟演示</el-button>
          <el-button size="large" plain @click="router.push('/agent/console')">进入坐席现场</el-button>
        </div>
      </div>
      <div class="hero-signal">
        <div class="signal-head"><span>知识检索</span><strong :class="{ degraded: tenantStore.degradation_alert }"><i></i> {{ tenantStore.degradation_alert ? '检索降级' : '检索正常' }}</strong></div>
        <ServiceFlowRail active="handoff" compact />
        <div class="tenant-strip"><span>{{ PLAN_LABEL[tenantStore.plan_type] || tenantStore.plan_type || '—' }}</span><span>{{ ROLE_LABEL[tenantStore.current_user?.role || ''] || '当前成员' }}</span><span class="mono-data">TENANT {{ tenantStore.tenant_id || '—' }}</span></div>
      </div>
    </section>

    <div class="page-container">
      <div class="alerts">
        <el-alert v-if="tenantStore.degradation_alert" type="error" :closable="false" show-icon title="知识检索处于降级状态" description="业务操作仍可使用；建议优先处理知识基础设施告警。" />
        <el-alert v-if="tenantStore.warning_banner" type="warning" :closable="false" show-icon :title="`本月会话用量已达 ${tenantStore.warning_banner}`" description="请查看用量趋势并评估是否升级套餐。" />
      </div>

      <section v-if="isAdmin" class="metric-grid" v-loading="analyticsLoading">
        <SignalMetric label="自动解决率" :value="`${summary.auto_resolution_rate || 0}%`" note="不需人工介入的已解决会话" trend="30 天" @click="router.push('/analytics/overview')" />
        <SignalMetric label="等待人工" :value="summary.waiting_human || 0" note="需要坐席立即查看" tone="amber" @click="router.push('/agent/console')" />
        <SignalMetric label="待审批退款" :value="summary.pending_refunds || 0" note="资金动作需要人工确认" tone="coral" @click="router.push('/order/refunds')" />
        <SignalMetric label="知识健康" :value="summary.knowledge_issues ? `${summary.knowledge_issues} 项` : '良好'" :note="summary.knowledge_issues ? '有文档待处理' : '当前无阻断问题'" tone="ink" @click="router.push('/knowledge/quality')" />
      </section>

      <section class="workspace-grid">
        <div class="surface-card attention-card">
          <div class="card-head"><div><p class="eyebrow">ACTION QUEUE</p><h2>今天需要处理</h2></div><el-button v-if="isAdmin" link type="primary" @click="loadDashboard">刷新信号</el-button></div>
          <div v-if="actionItems.length" class="attention-list">
            <button v-for="item in actionItems" :key="item.kind" type="button" class="attention-item" @click="router.push(item.path)">
              <span class="attention-indicator" :class="item.severity"></span>
              <span><strong>{{ item.title }}</strong><small>{{ actionHint(item.kind) }}</small></span>
              <b class="mono-data">{{ item.count }}</b><span>→</span>
            </button>
          </div>
          <div v-else class="all-clear"><span>✓</span><div><strong>当前没有阻断事项</strong><p>你可以运行演示剧本，或进入服务现场抽查会话质量。</p></div></div>
        </div>

        <div v-if="isAdmin" class="surface-card launch-card">
          <div class="card-head"><div><p class="eyebrow">GO LIVE</p><h2>上线准备度</h2></div><strong class="readiness mono-data">{{ readiness }}%</strong></div>
          <div class="readiness-track"><i :style="{ width: `${readiness}%` }"></i></div>
          <div class="check-grid">
            <button v-for="check in wizardChecks" :key="check.step" type="button" :class="{ ready: check.ready }" @click="router.push(check.action_path)">
              <span>{{ check.ready ? '✓' : '·' }}</span><span><strong>{{ check.label }}</strong><small>{{ check.detail }}</small></span>
            </button>
          </div>
          <el-button v-if="wizardIncomplete" class="launch-button" type="primary" plain @click="router.push('/setup')">继续完成配置</el-button>
        </div>
      </section>

      <section class="quick-section">
        <div class="section-head"><div><p class="eyebrow">WORKSPACES</p><h2>常用工作区</h2></div></div>
        <div class="quick-grid">
          <button v-for="entry in quickEntries" :key="entry.path" type="button" class="quick-card" @click="router.push(entry.path)">
            <span class="quick-code mono-data">{{ entry.code }}</span><span><strong>{{ entry.name }}</strong><small>{{ entry.desc }}</small></span><span class="quick-arrow">↗</span>
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useTenantStore } from '@/store/modules/tenant'
import { getWizardStatus, type WizardStatus } from '@/api/tenant'
import { getAnalyticsOverview, type AnalyticsOverview, type AnalyticsSummary } from '@/api/analytics'
import ServiceFlowRail from '@/components/common/ServiceFlowRail.vue'
import SignalMetric from '@/components/common/SignalMetric.vue'

const PLAN_LABEL: Record<string, string> = { trial: '试用版', basic: '基础版', pro: '专业版', enterprise: '企业版' }
const ROLE_LABEL: Record<string, string> = { admin: '管理员视角', agent: '坐席视角' }
const tenantStore = useTenantStore()
const router = useRouter()
const analytics = ref<AnalyticsOverview | null>(null)
const wizard = ref<WizardStatus | null>(null)
const analyticsLoading = ref(false)
const isAdmin = computed(() => tenantStore.current_user?.role === 'admin')
const summary = computed<Partial<AnalyticsSummary>>(() => analytics.value?.summary || {})
const actionItems = computed(() => {
  if (!isAdmin.value) return [
    { kind: 'waiting_human', title: '等待人工接管', count: '查看', path: '/agent/console', severity: 'high' as const },
    { kind: 'tickets', title: '工单看板', count: '进入', path: '/ticket/list', severity: 'medium' as const },
  ]
  return analytics.value?.attention_items || []
})
const wizardChecks = computed(() => (wizard.value?.checks || []).filter((item) => item.step !== 'done'))
const readiness = computed(() => {
  const checks = wizardChecks.value
  return checks.length ? Math.round(checks.filter((item) => item.ready).length / checks.length * 100) : (wizard.value?.completed ? 100 : 0)
})
const wizardIncomplete = computed(() => wizard.value ? !wizard.value.completed || readiness.value < 100 : false)
const nowHour = new Date().getHours()
const greeting = nowHour < 11 ? '早上好' : nowHour < 18 ? '下午好' : '晚上好'
const todayLabel = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(new Date())

const quickEntries = computed(() => [
  { code: 'KB', name: '知识库', desc: '管理文档、版本与解析状态', path: '/knowledge/list', adminOnly: true },
  { code: 'TK', name: '工单看板', desc: '跟进待办与服务恢复', path: isAdmin.value ? '/ticket/list' : '/agent/tickets' },
  { code: 'OR', name: '订单列表', desc: '查询履约与售后状态', path: isAdmin.value ? '/order/list' : '/agent/orders' },
  { code: 'TM', name: '成员邀请', desc: '配置人工接管团队', path: '/onboarding/members', adminOnly: true },
  { code: 'QA', name: '回答验证台', desc: '验证知识答案与引用', path: '/knowledge/search-test', adminOnly: true },
  { code: 'OP', name: '运营分析助手', desc: '把数据整理成行动建议', path: '/agent-report', adminOnly: true },
].filter((entry) => !entry.adminOnly || isAdmin.value))

function actionHint(kind: string) {
  return ({ waiting_human: '先接住正在等待的客户', pending_refunds: '核对金额与证据后作出决定', knowledge_issues: '修复失败或过期文档', quota: '查看趋势并调整套餐', tickets: '处理未完成的客户问题' } as Record<string, string>)[kind] || '打开工作区处理'
}

async function loadDashboard() {
  analyticsLoading.value = true
  try {
    // 当前用户身份决定是否加载管理员驾驶舱。首次进入时必须先拿到租户首页，
    // 否则 isAdmin 会在 null 身份上被误判为 false，首屏指标会一直停留在 0。
    if (!tenantStore.tenant_id || !tenantStore.current_user) await tenantStore.fetchHome()
    if (!isAdmin.value) return

    const results = await Promise.allSettled([
      getAnalyticsOverview(30).then((response) => { analytics.value = response.data }),
      getWizardStatus().then((response) => { wizard.value = response.data }),
    ])
    const failed = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
    if (failed) throw failed.reason
  } catch (error: any) { if (error?.status !== 401) ElMessage.error(error?.message || '加载驾驶舱失败') }
  finally { analyticsLoading.value = false }
}

onMounted(loadDashboard)
</script>

<style scoped>
.home-page { min-height: 100%; }.command-hero { display: grid; grid-template-columns: minmax(0, 1fr) minmax(440px, .8fr); gap: 30px; align-items: center; min-height: 315px; padding: 42px clamp(24px, 4vw, 58px); color: #fff; background: var(--zh-ink); }
.hero-copy h1 { margin: 10px 0 12px; font-size: clamp(27px, 3.2vw, 46px); letter-spacing: -.045em; }.hero-copy > p:not(.eyebrow) { max-width: 680px; color: #a7bbbf; line-height: 1.8; }.hero-actions { display: flex; gap: 10px; margin-top: 25px; }.hero-actions :deep(.el-button.is-plain) { color: #cde0e2; background: rgba(255,255,255,.05); border-color: rgba(255,255,255,.18); }
.hero-signal { padding: 18px; background: rgba(255,255,255,.035); border: 1px solid rgba(255,255,255,.09); border-radius: 18px; }.signal-head { display: flex; justify-content: space-between; margin-bottom: 13px; color: #759095; font-size: 10px; }.signal-head strong { color: #a9e8c8; }.signal-head strong.degraded { color: #ffb4ab; }.signal-head i { display: inline-block; width: 6px; height: 6px; margin-right: 5px; background: #63d49c; border-radius: 50%; }.signal-head strong.degraded i { background: #e96555; }.tenant-strip { display: flex; gap: 8px; margin-top: 12px; }.tenant-strip span { padding: 5px 7px; color: #769094; font-size: 8px; background: rgba(255,255,255,.04); border-radius: 6px; }
.alerts { display: grid; gap: 10px; margin-bottom: 16px; }.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 13px; }.workspace-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(330px, .75fr); gap: 16px; margin-top: 16px; }.attention-card, .launch-card { padding: 21px; }.card-head, .section-head { display: flex; justify-content: space-between; align-items: flex-start; }.card-head h2, .section-head h2 { margin-top: 4px; font-size: 18px; letter-spacing: -.02em; }
.attention-list { margin-top: 15px; }.attention-item { display: grid; grid-template-columns: auto 1fr auto auto; gap: 12px; align-items: center; width: 100%; padding: 13px 5px; text-align: left; color: var(--zh-text-primary); background: transparent; border: 0; border-top: 1px solid var(--zh-border-light); cursor: pointer; }.attention-item:hover { background: #f5faf7; }.attention-item > span:nth-child(2) { display: grid; }.attention-item small { margin-top: 3px; color: var(--zh-text-secondary); font-size: 10px; }.attention-indicator { width: 8px; height: 8px; background: var(--zh-amber); border-radius: 3px; }.attention-indicator.high { background: var(--zh-coral); }.attention-item b { font-size: 16px; }.all-clear { display: flex; gap: 15px; align-items: center; min-height: 150px; color: var(--zh-text-regular); }.all-clear > span { display: grid; place-items: center; width: 46px; height: 46px; color: var(--zh-primary); font-size: 20px; background: var(--zh-primary-bg); border-radius: 15px; }.all-clear p { margin-top: 5px; color: var(--zh-text-secondary); font-size: 11px; }
.readiness { color: var(--zh-primary); font-size: 25px; }.readiness-track { height: 6px; margin: 16px 0; overflow: hidden; background: #e4ebe8; border-radius: 99px; }.readiness-track i { display: block; height: 100%; background: linear-gradient(90deg, var(--zh-primary), #5fd0bd); border-radius: inherit; transition: width .4s; }.check-grid { display: grid; gap: 4px; }.check-grid button { display: grid; grid-template-columns: auto 1fr; gap: 9px; align-items: center; padding: 7px; text-align: left; color: var(--zh-text-regular); background: transparent; border: 0; border-radius: 8px; cursor: pointer; }.check-grid button:hover { background: #f4f8f5; }.check-grid button > span:first-child { display: grid; place-items: center; width: 20px; height: 20px; color: var(--zh-text-placeholder); background: #eef1ef; border-radius: 6px; }.check-grid button.ready > span:first-child { color: #fff; background: var(--zh-primary); }.check-grid button > span:last-child { display: grid; }.check-grid strong { font-size: 11px; }.check-grid small { overflow: hidden; color: var(--zh-text-secondary); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }.launch-button { width: 100%; margin-top: 13px; }
.quick-section { margin-top: 29px; }.quick-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 11px; margin-top: 13px; }.quick-card { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center; min-width: 0; padding: 16px; text-align: left; color: var(--zh-text-primary); background: rgba(255,254,250,.72); border: 1px solid var(--zh-border-light); border-radius: 12px; cursor: pointer; transition: transform .18s, border .18s; }.quick-card:hover { transform: translateY(-2px); border-color: #accdc5; }.quick-code { display: grid; place-items: center; width: 35px; height: 35px; color: var(--zh-primary); font-size: 10px; background: var(--zh-primary-bg); border-radius: 10px; }.quick-card > span:nth-child(2) { display: grid; min-width: 0; }.quick-card strong { font-size: 12px; }.quick-card small { overflow: hidden; margin-top: 3px; color: var(--zh-text-secondary); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }.quick-arrow { color: var(--zh-text-placeholder); }
@media (max-width: 1150px) { .command-hero { grid-template-columns: 1fr; }.metric-grid { grid-template-columns: repeat(2,1fr); }.workspace-grid { grid-template-columns: 1fr; } }
@media (max-width: 720px) { .metric-grid, .quick-grid { grid-template-columns: 1fr; }.command-hero { padding: 28px 20px; }.hero-actions { flex-wrap: wrap; } }
</style>
