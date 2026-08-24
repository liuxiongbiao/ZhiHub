<template>
  <div>
    <PageHeader title="配置向导" subtitle="每一步都用真实配置校验，通过后才算具备上线条件">
      <template #actions><el-button @click="loadStatus">重新检查</el-button></template>
    </PageHeader>
    <div class="page-container" v-loading="loading">
      <section class="setup-hero surface-card">
        <div><p class="eyebrow">GO LIVE READINESS</p><h1>{{ readyCount === checks.length && checks.length ? '系统已具备对外服务条件' : '把 AI 客服真正接到业务里' }}</h1><p>不是点击“下一步”就算完成。智枢会检查知识、业务工具、人工团队和客户入口是否真的可用。</p></div>
        <div class="readiness-score"><strong class="mono-data">{{ readiness }}%</strong><span>{{ readyCount }} / {{ checks.length }} 项就绪</span></div>
      </section>

      <div class="readiness-track"><i :style="{ width: `${readiness}%` }"></i></div>

      <section class="check-grid">
        <article v-for="(check, index) in checks" :key="check.step" class="check-card surface-card" :class="{ ready: check.ready, current: isCurrent(check.step) }">
          <div class="check-top"><span class="step-index mono-data">0{{ index + 1 }}</span><span class="check-state">{{ check.ready ? '✓ 已验证' : isCurrent(check.step) ? '当前步骤' : '待完成' }}</span></div>
          <div class="check-icon">{{ STEP_ICON[check.step] || '·' }}</div>
          <h2>{{ check.label }}</h2><p>{{ check.description }}</p>
          <div class="check-detail"><span :class="{ ok: check.ready }"></span>{{ check.detail }}</div>
          <el-button v-if="!check.ready" class="action-button" plain @click="router.push(check.action_path)">{{ actionLabel(check.step) }}</el-button>
          <el-button v-else class="action-button" text type="success" @click="router.push(check.action_path)">查看配置 →</el-button>
        </article>
      </section>

      <section class="next-panel surface-card">
        <div><p class="eyebrow">CURRENT CHECKPOINT</p><h2>{{ currentTitle }}</h2><p>{{ currentDescription }}</p><el-alert v-if="currentMissing.length" type="warning" :closable="false" :title="`还需完成：${currentMissing.map((item) => item.label).join('、')}`" /></div>
        <div class="next-actions"><el-button v-if="fullyCompleted" type="primary" size="large" @click="router.push('/')">返回服务驾驶舱</el-button><el-button v-else type="primary" size="large" :loading="advancing" :disabled="!currentReady" @click="handleNext">{{ allReady ? '完成上线验证' : '验证并进入下一步' }}</el-button><small v-if="!currentReady">完成上方当前步骤后，按钮会自动可用</small></div>
      </section>

      <section class="explain-row"><div><strong>为什么需要真实校验？</strong><p>避免演示时才发现文档未解析、没有坐席或 Widget Key 尚未生成。</p></div><div><strong>会改动现有业务吗？</strong><p>向导只读取配置并推进状态，不会删除文档、成员或业务数据。</p></div><div><strong>可以以后再完善吗？</strong><p>可以；但只有所有关键项通过，系统才会标记为真正可上线。</p></div></section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getWizardStatus, advanceWizard, type WizardStatus } from '@/api/tenant'
import PageHeader from '@/components/common/PageHeader.vue'

const router = useRouter()
const status = ref<WizardStatus | null>(null)
const loading = ref(false)
const advancing = ref(false)
const STEP_ICON: Record<string,string> = { basic: '企', knowledge: '知', mcp: '业', team: '人', channel: '窗' }
const checks = computed(() => (status.value?.checks || []).filter((item) => item.step !== 'done'))
const readyCount = computed(() => checks.value.filter((item) => item.ready).length)
const readiness = computed(() => checks.value.length ? Math.round(readyCount.value / checks.value.length * 100) : 0)
const allReady = computed(() => checks.value.length > 0 && readyCount.value === checks.value.length)
const fullyCompleted = computed(() => allReady.value && Boolean(status.value?.completed))
function isCurrent(step: string) { return status.value?.current_step === step || (status.value?.current_step === 'team' && step === 'channel') }
const currentMissing = computed(() => checks.value.filter((item) => isCurrent(item.step) && !item.ready))
const currentReady = computed(() => currentMissing.value.length === 0)
const currentTitle = computed(() => allReady.value ? '所有上线条件都已通过' : `正在验证：${checks.value.filter((item) => isCurrent(item.step)).map((item) => item.label).join(' + ') || '企业信息'}`)
const currentDescription = computed(() => allReady.value ? '知识、业务、人工与渠道已经形成完整闭环。' : '系统会读取真实配置，不允许通过空步骤跳过上线条件。')
function actionLabel(step: string) { return ({ basic: '检查企业信息', knowledge: '上传或处理文档', mcp: '查看业务演示', team: '邀请坐席成员', channel: '生成 Widget 通道' } as Record<string,string>)[step] || '去完成' }
async function loadStatus() { loading.value = true; try { status.value = (await getWizardStatus()).data } catch (error: any) { ElMessage.error(error?.message || '加载配置状态失败') } finally { loading.value = false } }
async function handleNext() { advancing.value = true; try { status.value = (await advanceWizard()).data; ElMessage.success('当前步骤已通过真实配置校验') } catch (error: any) { ElMessage.warning(error?.message || '当前步骤尚未完成') ; await loadStatus() } finally { advancing.value = false } }
onMounted(loadStatus)
</script>

<style scoped>
.setup-hero { display: flex; justify-content: space-between; gap: 30px; align-items: center; padding: 28px 32px; color: #fff; background: var(--zh-ink); }.setup-hero h1 { margin: 8px 0; font-size: clamp(25px,3vw,38px); letter-spacing: -.04em; }.setup-hero p:not(.eyebrow) { max-width: 690px; color: #9eb4b8; line-height: 1.7; }.readiness-score { display: grid; place-items: end; flex: 0 0 auto; }.readiness-score strong { color: #8ce5d6; font-size: 43px; }.readiness-score span { color: #769095; font-size: 10px; }.readiness-track { height: 7px; margin: 13px 4px 20px; overflow: hidden; background: #dfe7e4; border-radius: 99px; }.readiness-track i { display: block; height: 100%; background: linear-gradient(90deg,var(--zh-primary),#55cfbb); border-radius: inherit; transition: width .35s; }
.check-grid { display: grid; grid-template-columns: repeat(5,minmax(0,1fr)); gap: 12px; }.check-card { position: relative; overflow: hidden; padding: 17px; }.check-card.current { border-color: #88c8bb; box-shadow: 0 0 0 2px rgba(15,143,130,.1), var(--zh-shadow); }.check-card.ready::before { content: ''; position: absolute; inset: 0 0 auto; height: 3px; background: var(--zh-signal); }.check-top { display: flex; justify-content: space-between; color: var(--zh-text-placeholder); font-size: 8px; }.check-state { font-weight: 700; }.ready .check-state { color: var(--zh-primary); }.check-icon { display: grid; place-items: center; width: 39px; height: 39px; margin: 19px 0 13px; color: #fff; font-size: 12px; font-weight: 800; background: #8ca09c; border-radius: 12px; }.ready .check-icon { background: var(--zh-primary); }.check-card h2 { font-size: 14px; }.check-card > p { height: 48px; margin-top: 5px; color: var(--zh-text-secondary); font-size: 9px; line-height: 1.6; }.check-detail { display: flex; gap: 7px; align-items: flex-start; min-height: 41px; padding: 9px; color: var(--zh-text-regular); font-size: 8px; line-height: 1.5; background: #f4f7f5; border-radius: 8px; }.check-detail span { width: 6px; height: 6px; flex: 0 0 auto; margin-top: 3px; background: var(--zh-amber); border-radius: 50%; }.check-detail span.ok { background: var(--zh-signal); }.action-button { width: 100%; margin-top: 10px; font-size: 10px; }
.next-panel { display: flex; justify-content: space-between; gap: 30px; align-items: center; margin-top: 16px; padding: 22px 25px; }.next-panel h2 { margin: 4px 0; font-size: 18px; }.next-panel > div:first-child > p:not(.eyebrow) { margin-bottom: 11px; color: var(--zh-text-secondary); font-size: 10px; }.next-actions { display: grid; justify-items: stretch; min-width: 220px; }.next-actions small { margin-top: 7px; color: var(--zh-text-placeholder); font-size: 8px; text-align: center; }.explain-row { display: grid; grid-template-columns: repeat(3,1fr); gap: 18px; margin-top: 24px; padding: 0 6px; }.explain-row div { padding-left: 13px; border-left: 2px solid #cad8d4; }.explain-row strong { font-size: 10px; }.explain-row p { margin-top: 4px; color: var(--zh-text-secondary); font-size: 8px; line-height: 1.6; }
@media (max-width: 1150px) { .check-grid { grid-template-columns: repeat(3,1fr); } }.check-card:last-child { }
@media (max-width: 720px) { .setup-hero, .next-panel { align-items: flex-start; flex-direction: column; }.readiness-score { place-items: start; }.check-grid, .explain-row { grid-template-columns: 1fr; }.next-actions { width: 100%; }.check-card > p { height: auto; } }
</style>
