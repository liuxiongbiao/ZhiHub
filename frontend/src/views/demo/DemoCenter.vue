<template>
  <div class="demo-page">
    <section class="demo-hero">
      <div>
        <p class="eyebrow">GUIDED PRODUCT STORY · 约 3 分钟</p>
        <h1>一段对话，看清智枢的价值</h1>
        <p>可信回答、业务执行、风险转人工——沿着同一个客户问题，完整讲完服务闭环。</p>
      </div>
      <div class="hero-actions">
        <el-button plain @click="openWidget">打开客户入口</el-button>
        <el-button type="primary" :disabled="!status?.can_restore" :loading="restoring" @click="confirmRestore">恢复演示基线</el-button>
      </div>
    </section>

    <div v-if="status && !status.is_demo" class="page-container">
      <el-result icon="warning" title="当前不是演示租户" sub-title="演示中心只在本机演示租户中开放，正式租户数据不会被改写。"><template #extra><el-button type="primary" @click="router.push('/')">返回驾驶舱</el-button></template></el-result>
    </div>

    <div v-else class="demo-workspace" v-loading="loading">
      <aside class="story-nav surface-card">
        <div class="story-progress"><span>演示进度</span><strong class="mono-data">0{{ activeIndex + 1 }} / 03</strong></div>
        <div class="progress-track"><i :style="{ width: `${(activeIndex + 1) / 3 * 100}%` }"></i></div>
        <button v-for="(scenario, index) in scenarios" :key="scenario.id" type="button" class="story-step" :class="{ active: activeIndex === index, done: activeIndex > index }" @click="activeIndex = index">
          <span class="step-no mono-data">0{{ index + 1 }}</span>
          <span><strong>{{ scenario.title }}</strong><small>{{ scenario.outcome }}</small></span>
          <b>{{ activeIndex > index ? '✓' : '→' }}</b>
        </button>

        <div class="demo-facts">
          <p class="eyebrow">DEMO FACTS</p>
          <dl><div><dt>订单</dt><dd class="mono-data">{{ status?.order_no || '—' }}</dd></div><div><dt>手机尾号</dt><dd class="mono-data">{{ status?.customer_phone_last4 || '—' }}</dd></div></dl>
        </div>
      </aside>

      <main class="conversation-stage surface-card">
        <header class="stage-head">
          <div><p class="eyebrow">SCENE {{ activeIndex + 1 }}</p><h2>{{ currentScenario?.title }}</h2></div>
          <el-tag :type="activeIndex === 2 ? 'warning' : 'success'" effect="light">{{ activeIndex === 0 ? '可信回答' : activeIndex === 1 ? '业务执行' : '人工兜底' }}</el-tag>
        </header>
        <ServiceFlowRail :active="activeRail" compact />

        <div class="conversation">
          <div class="message customer"><span class="avatar">客</span><div><small>客户 · 刚刚</small><p>{{ currentScenario?.prompt }}</p></div></div>
          <div class="decision-line"><span></span><em>{{ activeIndex === 0 ? '查找企业政策' : activeIndex === 1 ? '核验身份并查询订单' : '命中资金风险规则' }}</em><span></span></div>
          <div class="message assistant" :class="{ handoff: activeIndex === 2 }"><span class="avatar">枢</span><div><small>{{ activeIndex === 2 ? '智枢 · 转人工前说明' : '智枢 · 基于真实数据' }}</small><p>{{ assistantReply }}</p><div v-if="activeIndex === 0" class="source-chip">↳ 引用：七天无理由退货政策.md</div><div v-if="activeIndex === 1" class="business-chip"><span>订单状态</span><strong>{{ business?.order?.status === 'shipped' ? '已发货' : business?.order?.status || '已发货' }}</strong><span>物流</span><strong class="mono-data">{{ business?.order?.tracking_no || 'SF1234567890' }}</strong></div></div></div>
          <div v-if="activeIndex === 2" class="handoff-card"><span class="handoff-icon">↗</span><div><strong>完整上下文已交给人工</strong><p>客户无需重述问题；坐席可直接看到订单、对话和安全判断。</p></div><el-button type="warning" @click="switchToAgent">切换到坐席视角</el-button></div>
        </div>

        <footer class="stage-footer"><el-button :disabled="activeIndex === 0" @click="activeIndex--">上一幕</el-button><span>{{ currentScenario?.outcome }}</span><el-button v-if="activeIndex < 2" type="primary" @click="activeIndex++">下一幕</el-button><el-button v-else type="primary" @click="router.push('/analytics/overview')">查看运营结果</el-button></footer>
      </main>

      <aside class="evidence-panel surface-card">
        <div class="evidence-head"><p class="eyebrow">WHY IT WORKED</p><h2>{{ evidenceTitle }}</h2></div>
        <div class="evidence-body">
          <template v-if="activeIndex === 0">
            <div class="confidence-ring"><strong class="mono-data">93%</strong><span>回答置信度</span></div>
            <div class="evidence-row"><span>知识来源</span><strong>企业政策文档</strong></div><div class="evidence-row"><span>检索方式</span><strong>混合检索 + 精排</strong></div><div class="evidence-row"><span>回答边界</span><strong>只依据已发布内容</strong></div>
          </template>
          <template v-else-if="activeIndex === 1">
            <div class="order-proof"><span>ORD</span><div><small>查询对象</small><strong class="mono-data">{{ status?.order_no }}</strong></div></div>
            <div class="evidence-row"><span>身份校验</span><strong>订单号 + 手机尾号</strong></div><div class="evidence-row"><span>业务工具</span><strong>订单查询已成功</strong></div><div class="evidence-row"><span>数据结果</span><strong>金额、状态、物流</strong></div>
          </template>
          <template v-else>
            <div class="risk-badge">人工必须确认</div>
            <div class="evidence-row"><span>命中规则</span><strong>额外赔偿 / 资金操作</strong></div><div class="evidence-row"><span>系统动作</span><strong>停止自动承诺</strong></div><div class="evidence-row"><span>交接内容</span><strong>对话 + 订单 + 判断依据</strong></div>
          </template>
        </div>
        <el-collapse class="expert-trace">
          <el-collapse-item title="查看专家轨迹">
            <div v-if="currentTrace?.steps?.length" class="trace-list"><div v-for="step in currentTrace.steps" :key="step.step + step.label"><span></span><div><strong>{{ step.label }}</strong><small>{{ step.observation || step.technical_label || `${step.elapsed_ms || 0}ms` }}</small></div></div></div>
            <p v-else class="trace-empty">轨迹库暂不可用，演示故事仍可继续。</p>
          </el-collapse-item>
        </el-collapse>

        <div v-if="status?.widget_api_key" class="widget-key-box"><div><small>真实 Widget 通道</small><strong>演示 Key 已安全派生</strong></div><el-button link type="primary" @click="copyWidgetKey">复制并预览</el-button></div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getDemoStatus, restoreDemo, type DemoStatus } from '@/api/demo'
import { getSessionBusinessContext, getSessionDetail, getSessionList, getTrace, type SessionBusinessContext } from '@/api/session'
import type { Message } from '@/types/session'
import type { AgentTrace } from '@/types/trace'
import { useUserStore } from '@/store/modules/user'
import ServiceFlowRail from '@/components/common/ServiceFlowRail.vue'

const router = useRouter()
const userStore = useUserStore()
const status = ref<DemoStatus | null>(null)
const messages = ref<Message[]>([])
const traces = ref<AgentTrace[]>([])
const business = ref<SessionBusinessContext | null>(null)
const activeIndex = ref(0)
const loading = ref(false)
const restoring = ref(false)
const scenarios = computed(() => status.value?.scenarios || [])
const currentScenario = computed(() => scenarios.value[activeIndex.value])
const activeRail = computed(() => (['knowledge', 'action', 'handoff'] as const)[activeIndex.value])
const evidenceTitle = computed(() => ['答案可核验', '不是只会聊天', '风险不会失控'][activeIndex.value])
const assistantReply = computed(() => {
  const index = activeIndex.value * 2 + 1
  return messages.value[index]?.content || [
    '支持。商品签收后 7 天内且保持完好，可在订单页发起退货；审核后按指引寄回，仓库验收后 1–3 个工作日原路退款。',
    '订单 ORD-DEMO-0003 金额 899 元，当前已发货，物流单号 SF1234567890。',
    '现有政策无法确认额外赔偿金额，这属于需要人工判断的高风险售后事项。我已为你转接人工客服。',
  ][activeIndex.value]
})
const currentTrace = computed(() => {
  const intent = ['knowledge_consult', 'order_query', 'sensitive_action'][activeIndex.value]
  return traces.value.find((trace) => trace.intent === intent) || traces.value[activeIndex.value] || null
})

async function loadStory() {
  loading.value = true
  try {
    status.value = (await getDemoStatus()).data
    if (!status.value.is_demo) return
    const sessions = (await getSessionList({ page_size: 50 })).data.items
    const story = sessions.find((item) => item.status === 'waiting_human' && item.message_count >= 6)
    if (story) {
      const [detail, trace, context] = await Promise.allSettled([
        getSessionDetail(story.session_id), getTrace(story.session_id), getSessionBusinessContext(story.session_id),
      ])
      if (detail.status === 'fulfilled') messages.value = detail.value.data.messages
      if (trace.status === 'fulfilled') traces.value = trace.value.data.traces
      if (context.status === 'fulfilled') business.value = context.value.data
    }
  } catch (error: any) { ElMessage.error(error?.message || '加载演示故事失败') }
  finally { loading.value = false }
}

async function confirmRestore() {
  try { await ElMessageBox.confirm('将恢复带 demo_seed 标记的演示知识、会话、轨迹和趋势；真实交互不会被删除。', '恢复演示基线', { type: 'warning', confirmButtonText: '确认恢复', cancelButtonText: '取消' }) }
  catch { return }
  restoring.value = true
  try { await restoreDemo(); ElMessage.success('演示基线已恢复'); activeIndex.value = 0; await loadStory() }
  catch (error: any) { ElMessage.error(error?.message || '恢复失败') }
  finally { restoring.value = false }
}

async function switchToAgent() {
  try { await userStore.loginDemo('agent1'); ElMessage.success('已切换到一线坐席视角'); router.push('/agent/console') }
  catch (error: any) { ElMessage.error(error?.message || '角色切换失败') }
}
async function copyWidgetKey() {
  if (!status.value?.widget_api_key) return
  sessionStorage.setItem('zhihub:demo-widget-key', status.value.widget_api_key)
  try { await navigator.clipboard.writeText(status.value.widget_api_key); ElMessage.success('演示 Widget Key 已复制') } catch { ElMessage.info('已为真实预览自动填入 Key') }
  router.push('/widget/preview?mode=real')
}
function openWidget() { if (status.value?.widget_api_key) sessionStorage.setItem('zhihub:demo-widget-key', status.value.widget_api_key); router.push('/widget/preview?mode=real') }
watch(activeIndex, () => { document.querySelector('.conversation')?.scrollTo({ top: 0, behavior: 'smooth' }) })
onMounted(loadStory)
</script>

<style scoped>
.demo-page { min-height: 100%; }.demo-hero { display: flex; justify-content: space-between; gap: 30px; align-items: flex-end; padding: 34px clamp(24px,4vw,52px) 28px; color: #fff; background: var(--zh-ink); }.demo-hero h1 { margin: 8px 0 8px; font-size: clamp(27px,3.3vw,44px); letter-spacing: -.045em; }.demo-hero p:not(.eyebrow) { color: #9fb4b8; }.hero-actions { display: flex; gap: 9px; flex-shrink: 0; }.hero-actions :deep(.el-button.is-plain) { color: #d3e1e2; background: rgba(255,255,255,.05); border-color: rgba(255,255,255,.17); }
.demo-workspace { display: grid; grid-template-columns: 240px minmax(480px,1fr) 290px; gap: 14px; min-height: calc(100vh - 170px); padding: 16px; }.story-nav, .conversation-stage, .evidence-panel { min-width: 0; overflow: hidden; }.story-nav { padding: 18px 13px; }.story-progress { display: flex; justify-content: space-between; color: var(--zh-text-secondary); font-size: 10px; }.story-progress strong { color: var(--zh-text-primary); }.progress-track { height: 4px; margin: 10px 1px 20px; background: #e6ece9; border-radius: 99px; }.progress-track i { display: block; height: 100%; background: var(--zh-primary); border-radius: inherit; transition: width .25s; }.story-step { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: start; width: 100%; padding: 14px 9px; text-align: left; color: var(--zh-text-regular); background: transparent; border: 0; border-top: 1px solid var(--zh-border-light); cursor: pointer; }.story-step.active { color: var(--zh-text-primary); background: var(--zh-primary-bg); border-radius: 10px; }.story-step.done { color: var(--zh-primary); }.step-no { font-size: 9px; }.story-step > span:nth-child(2) { display: grid; }.story-step strong { font-size: 11px; }.story-step small { margin-top: 5px; color: var(--zh-text-secondary); font-size: 9px; line-height: 1.5; }.story-step b { font-size: 11px; }.demo-facts { margin: 26px 5px 0; padding-top: 16px; border-top: 1px solid var(--zh-border-light); }.demo-facts dl { margin-top: 10px; }.demo-facts dl div { display: flex; justify-content: space-between; margin-top: 8px; font-size: 9px; }.demo-facts dt { color: var(--zh-text-secondary); }.demo-facts dd { font-size: 8px; }
.conversation-stage { display: flex; flex-direction: column; padding: 20px; }.stage-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }.stage-head h2 { margin-top: 3px; font-size: 19px; }.conversation-stage :deep(.service-rail) { padding: 11px; background: var(--zh-ink); border-radius: 13px; }.conversation { flex: 1; overflow-y: auto; padding: 26px clamp(10px,3vw,42px); }.message { display: flex; gap: 11px; align-items: flex-start; margin-bottom: 21px; }.message.customer { flex-direction: row-reverse; }.message > div { max-width: 82%; }.message small { color: var(--zh-text-secondary); font-size: 9px; }.message p { margin-top: 5px; padding: 12px 14px; color: var(--zh-text-primary); font-size: 13px; line-height: 1.7; background: #f1f4f2; border-radius: 3px 13px 13px 13px; }.message.customer p { color: #fff; background: var(--zh-primary); border-radius: 13px 3px 13px 13px; }.message.handoff p { background: var(--zh-amber-bg); border: 1px solid #f3dfb5; }.avatar { display: grid; place-items: center; width: 30px; height: 30px; flex: 0 0 auto; color: #fff; font-size: 10px; font-weight: 800; background: var(--zh-ink-soft); border-radius: 9px; }.customer .avatar { background: #7c918d; }.decision-line { display: flex; gap: 8px; align-items: center; margin: 0 40px 20px; color: var(--zh-primary); font-size: 9px; text-align: center; }.decision-line span { flex: 1; border-top: 1px dashed #b9d4ce; }.decision-line em { font-style: normal; }.source-chip, .business-chip { margin-top: 7px; padding: 8px 10px; color: var(--zh-primary); font-size: 9px; background: var(--zh-primary-bg); border-radius: 8px; }.business-chip { display: grid; grid-template-columns: auto 1fr auto 1fr; gap: 5px 10px; }.business-chip span { color: var(--zh-text-secondary); }.handoff-card { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center; margin: 8px 40px; padding: 14px; background: var(--zh-amber-bg); border: 1px solid #efd9a8; border-radius: 12px; }.handoff-icon { display: grid; place-items: center; width: 33px; height: 33px; color: #fff; background: var(--zh-amber); border-radius: 10px; }.handoff-card strong { font-size: 11px; }.handoff-card p { margin-top: 3px; color: #8b6b32; font-size: 9px; }.stage-footer { display: flex; justify-content: space-between; gap: 12px; align-items: center; padding-top: 13px; border-top: 1px solid var(--zh-border-light); }.stage-footer span { color: var(--zh-text-secondary); font-size: 9px; text-align: center; }
.evidence-panel { display: flex; flex-direction: column; padding: 19px; }.evidence-head h2 { margin-top: 5px; font-size: 18px; }.evidence-body { margin-top: 18px; }.confidence-ring { display: grid; place-items: center; width: 112px; height: 112px; margin: 5px auto 22px; background: radial-gradient(circle at center, var(--zh-bg-card) 57%, transparent 58%), conic-gradient(var(--zh-primary) 0 93%, #e2e9e6 93%); border-radius: 50%; }.confidence-ring strong { font-size: 24px; }.confidence-ring span { margin-top: -23px; color: var(--zh-text-secondary); font-size: 8px; }.evidence-row { display: flex; justify-content: space-between; gap: 8px; padding: 11px 0; border-top: 1px solid var(--zh-border-light); font-size: 9px; }.evidence-row span { color: var(--zh-text-secondary); }.evidence-row strong { text-align: right; }.order-proof { display: flex; gap: 11px; align-items: center; margin-bottom: 16px; padding: 13px; background: var(--zh-primary-bg); border-radius: 12px; }.order-proof > span { display: grid; place-items: center; width: 39px; height: 39px; color: #fff; font-size: 9px; font-weight: 800; background: var(--zh-primary); border-radius: 10px; }.order-proof div { display: grid; }.order-proof small { color: var(--zh-text-secondary); font-size: 8px; }.order-proof strong { margin-top: 2px; font-size: 10px; }.risk-badge { margin-bottom: 16px; padding: 17px; color: #9b5e16; font-size: 13px; font-weight: 800; text-align: center; background: var(--zh-amber-bg); border: 1px solid #efd8a8; border-radius: 12px; }.expert-trace { margin-top: auto; padding-top: 18px; border-top: 1px solid var(--zh-border-light); }.trace-list > div { display: flex; gap: 8px; padding: 7px 0; }.trace-list > div > span { width: 6px; height: 6px; margin-top: 5px; background: var(--zh-primary); border-radius: 50%; }.trace-list > div > div { display: grid; }.trace-list strong { font-size: 9px; }.trace-list small, .trace-empty { margin-top: 2px; color: var(--zh-text-secondary); font-size: 8px; line-height: 1.5; }.widget-key-box { display: flex; justify-content: space-between; gap: 8px; align-items: center; margin-top: 13px; padding: 10px; background: #f4f7f5; border-radius: 10px; }.widget-key-box div { display: grid; }.widget-key-box small { color: var(--zh-text-secondary); font-size: 8px; }.widget-key-box strong { font-size: 9px; }
@media (max-width: 1180px) { .demo-workspace { grid-template-columns: 210px 1fr; }.evidence-panel { grid-column: 1 / -1; }.evidence-body { display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; } }
@media (max-width: 780px) { .demo-hero { align-items: flex-start; flex-direction: column; }.demo-workspace { grid-template-columns: 1fr; }.story-nav { display: none; }.conversation { padding: 20px 0; }.handoff-card { margin: 8px 0; grid-template-columns: auto 1fr; }.handoff-card .el-button { grid-column: 1 / -1; } }
</style>
