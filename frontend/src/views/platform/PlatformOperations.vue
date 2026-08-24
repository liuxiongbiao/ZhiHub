<template>
  <div>
    <PageHeader title="平台运营入口" subtitle="使用独立运营令牌查看跨租户指标和处理企业版申请" />
    <div class="page-container">
      <section v-if="!connected" class="token-gate surface-card">
        <span class="gate-mark">OPS</span><div><p class="eyebrow">SECOND FACTOR</p><h1>验证平台运营权限</h1><p>这里跨越租户边界，因此除管理员登录态外，还需要独立的平台运营令牌。令牌只保存在当前页面内存中。</p><div class="token-row"><el-input v-model="platformToken" type="password" show-password size="large" placeholder="输入 PLATFORM_OPS_TOKEN" @keyup.enter="connect" /><el-button type="primary" size="large" :loading="loading" @click="connect">验证并进入</el-button></div></div>
      </section>

      <template v-else>
        <div class="ops-bar"><div><span class="online-dot"></span><strong>平台运营视图已连接</strong><small>关闭或刷新页面后需重新验证</small></div><el-button plain @click="disconnect">退出平台视图</el-button></div>
        <section class="metric-grid">
          <SignalMetric label="活跃租户" :value="activeTenants" note="当前套餐分布中的有效租户" tone="ink" />
          <SignalMetric label="待企业版审批" :value="pending.length" note="等待议价或激活" :tone="pending.length ? 'amber' : 'teal'" />
          <SignalMetric label="平台自动解决率" :value="`${overview?.summary.auto_resolution_rate || 0}%`" note="近 30 天全平台终态会话" />
          <SignalMetric label="平台事件量" :value="overview?.summary.event_count || 0" note="近 30 天运营事件" tone="ink" />
        </section>

        <section class="ops-grid">
          <div class="surface-card pending-card">
            <div class="card-head"><div><p class="eyebrow">ENTERPRISE QUEUE</p><h2>企业版申请</h2></div><el-button link type="primary" @click="loadData">刷新</el-button></div>
            <el-table :data="pending" v-loading="loading" empty-text="当前没有待审批申请">
              <el-table-column prop="tenant_name" label="企业" min-width="150" />
              <el-table-column label="申请" width="110"><template #default="{ row }"><el-tag type="warning" size="small">{{ row.pending_plan === 'enterprise' ? '升级企业版' : '新企业版' }}</el-tag></template></el-table-column>
              <el-table-column prop="created_at" label="进入队列" min-width="150"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column>
              <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link type="primary" @click="approve(row)">批准</el-button></template></el-table-column>
            </el-table>
          </div>
          <div class="surface-card plan-card"><div class="card-head"><div><p class="eyebrow">TENANT MIX</p><h2>套餐分布</h2></div></div><div class="plan-list"><div v-for="item in overview?.plan_distribution || []" :key="item.plan"><span><i :class="item.plan"></i>{{ planLabel(item.plan) }}</span><strong class="mono-data">{{ item.count }}</strong><em :style="{ width: `${planWidth(item.count)}%` }"></em></div></div><p class="ops-note">平台接口使用登录身份 + 独立运营令牌双重校验；普通租户管理员无法访问。</p></div>
        </section>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { approveEnterpriseTenant, getPendingEnterpriseTenants, type PendingEnterpriseTenant } from '@/api/tenant'
import { getPlatformAnalyticsOverview, type AnalyticsOverview } from '@/api/analytics'
import PageHeader from '@/components/common/PageHeader.vue'
import SignalMetric from '@/components/common/SignalMetric.vue'

const platformToken = ref('')
const connected = ref(false)
const loading = ref(false)
const pending = ref<PendingEnterpriseTenant[]>([])
const overview = ref<AnalyticsOverview | null>(null)
const activeTenants = computed(() => (overview.value?.plan_distribution || []).reduce((sum, item) => sum + item.count, 0))
const maxPlan = computed(() => Math.max(1, ...(overview.value?.plan_distribution || []).map((item) => item.count)))
function planWidth(count: number) { return count / maxPlan.value * 100 }
function planLabel(value: string) { return ({ trial: '试用版', basic: '基础版', pro: '专业版', enterprise: '企业版' } as Record<string,string>)[value] || value }
function formatTime(value: string) { return new Date(value).toLocaleString('zh-CN') }
async function loadData() { loading.value = true; try { const [tenants, analytics] = await Promise.all([getPendingEnterpriseTenants(platformToken.value), getPlatformAnalyticsOverview(30, platformToken.value)]); pending.value = tenants.data.items; overview.value = analytics.data; connected.value = true } catch (error: any) { connected.value = false; ElMessage.error(error?.message || '运营令牌验证失败') } finally { loading.value = false } }
async function connect() { if (!platformToken.value.trim()) { ElMessage.warning('请输入平台运营令牌'); return } await loadData() }
function disconnect() { platformToken.value = ''; pending.value = []; overview.value = null; connected.value = false }
async function approve(item: PendingEnterpriseTenant) { try { await ElMessageBox.confirm(`确认批准「${item.tenant_name}」的企业版申请？`, '企业版审批', { type: 'warning', confirmButtonText: '批准', cancelButtonText: '取消' }) } catch { return } try { await approveEnterpriseTenant(item.tenant_id, platformToken.value); ElMessage.success('企业版申请已批准'); await loadData() } catch (error: any) { ElMessage.error(error?.message || '审批失败') } }
</script>

<style scoped>
.token-gate { display: grid; grid-template-columns: auto minmax(0,560px); gap: 24px; align-items: center; max-width: 820px; margin: 8vh auto; padding: 36px; }.gate-mark { display: grid; place-items: center; width: 90px; height: 90px; color: #8ce6d7; font: 800 18px "Cascadia Code", monospace; background: var(--zh-ink); border-radius: 25px 25px 25px 7px; }.token-gate h1 { margin: 7px 0; font-size: 27px; }.token-gate p:not(.eyebrow) { color: var(--zh-text-regular); line-height: 1.7; }.token-row { display: flex; gap: 9px; margin-top: 18px; }.ops-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; padding: 11px 14px; background: var(--zh-primary-bg); border: 1px solid #c7e4dd; border-radius: 12px; }.ops-bar > div { display: grid; grid-template-columns: auto auto; gap: 2px 8px; align-items: center; }.ops-bar small { grid-column: 2; color: var(--zh-text-secondary); font-size: 9px; }.online-dot { width: 8px; height: 8px; grid-row: 1/3; background: var(--zh-signal); border-radius: 50%; }.metric-grid { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 13px; }.ops-grid { display: grid; grid-template-columns: minmax(0,1.35fr) minmax(300px,.65fr); gap: 14px; margin-top: 14px; }.pending-card, .plan-card { padding: 20px; }.card-head { display: flex; justify-content: space-between; }.card-head h2 { margin-top: 4px; font-size: 18px; }.pending-card :deep(.el-table) { margin-top: 13px; }.plan-list { margin-top: 17px; }.plan-list > div { position: relative; display: flex; justify-content: space-between; overflow: hidden; padding: 12px 4px; border-top: 1px solid var(--zh-border-light); }.plan-list span { display: flex; gap: 8px; align-items: center; font-size: 11px; }.plan-list i { width: 8px; height: 8px; background: #9dada9; border-radius: 3px; }.plan-list i.pro { background: var(--zh-primary); }.plan-list i.enterprise { background: var(--zh-amber); }.plan-list em { position: absolute; left: 0; bottom: 0; height: 2px; background: var(--zh-primary); }.ops-note { margin-top: 20px; padding: 12px; color: var(--zh-text-secondary); font-size: 9px; line-height: 1.7; background: #f4f7f5; border-radius: 9px; }
@media (max-width: 950px) { .metric-grid { grid-template-columns: repeat(2,1fr); }.ops-grid { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .token-gate { grid-template-columns: 1fr; }.token-row { flex-direction: column; }.metric-grid { grid-template-columns: 1fr; } }
</style>
