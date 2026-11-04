<!-- 套餐与规则配置：套餐升级（PRD §3.9）、退款阈值与数据导出，仅管理员。 -->
<template>
  <div>
    <PageHeader title="套餐与规则配置" subtitle="选择套餐、设置退款规则并管理企业数据" />
    <div class="page-container">
      <el-card v-if="!cancellation?.deletion_pending" shadow="never" class="settings-card plan-section">
        <template #header>
          <div class="card-header">
            <span>套餐升级（PRD 3.9）</span>
            <el-tag v-if="catalog?.pending_plan" type="warning">企业版议价处理中</el-tag>
          </div>
        </template>
        <div v-loading="loadingPlans" class="plan-grid">
          <div
            v-for="plan in catalog?.plans || []"
            :key="plan.plan_type"
            class="plan-card"
            :class="{ current: catalog?.current_plan === plan.plan_type }"
          >
            <div class="plan-title">
              <strong>{{ plan.name }}</strong>
              <el-tag v-if="catalog?.current_plan === plan.plan_type" type="success" size="small">当前套餐</el-tag>
              <el-tag v-else-if="catalog?.pending_plan === plan.plan_type" type="warning" size="small">待人工议价</el-tag>
            </div>
            <div class="plan-price">
              {{ plan.negotiable ? '人工议价' : `¥${plan.monthly_price}/月` }}
            </div>
            <ul class="plan-features">
              <li>月对话：{{ formatLimit(plan.conversation_quota, '条') }}</li>
              <li>知识库：{{ formatLimit(plan.knowledge_base_limit, '个') }}</li>
              <li>存储：{{ formatStorage(plan.storage_quota_mb) }}</li>
              <li>坐席：{{ formatLimit(plan.seat_limit, '席') }}</li>
            </ul>
            <el-button
              :type="catalog?.current_plan === plan.plan_type ? 'success' : 'primary'"
              :plain="catalog?.current_plan !== plan.plan_type"
              :disabled="!canUpgrade(plan)"
              :loading="upgradingPlan === plan.plan_type"
              @click="handleUpgrade(plan)"
            >
              {{ planButtonText(plan) }}
            </el-button>
          </div>
        </div>
      </el-card>

      <el-card v-if="!cancellation?.deletion_pending" shadow="never" class="settings-card">
        <template #header><span>退款审批阈值（PRD 3.6）</span></template>
        <p class="hint">单笔退款金额超过该阈值时，需企业管理员二次审批。</p>
        <el-form label-position="top" @submit.prevent="handleSave">
          <el-form-item label="大额退款审批阈值（元）">
            <el-input-number
              v-model="threshold"
              :min="0.01"
              :max="1000000"
              :precision="2"
              :step="100"
              style="width: 240px"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card shadow="never" class="settings-card">
        <template #header><span>租户数据导出（PRD 8.5）</span></template>
        <p class="hint">导出知识库、会话、工单、订单、退款审批、账单与用量数据（CSV 打包 ZIP）。</p>
        <el-button type="primary" plain :loading="exporting" @click="handleExport">导出全部数据 (ZIP)</el-button>
      </el-card>

      <el-card shadow="never" class="settings-card cancellation-card">
        <template #header>
          <div class="card-header">
            <span>租户注销（PRD 8.5）</span>
            <el-tag v-if="cancellation?.deletion_pending" type="danger">注销等待期</el-tag>
          </div>
        </template>
        <div v-if="loadingCancellation" class="hint">正在加载注销状态…</div>
        <template v-else-if="cancellation?.deletion_pending">
          <p class="danger-title">租户已进入注销等待期</p>
          <p class="hint">
            数据将在 {{ formatDate(cancellation.scheduled_deletion_at) }} 物理删除，
            预计剩余 {{ cancellation.days_remaining ?? 0 }} 天。等待期内仅可导出数据或撤销注销。
          </p>
          <el-button
            type="success"
            plain
            :disabled="!cancellation.can_revoke"
            :loading="revokingCancellation"
            @click="handleRevokeCancellation"
          >撤销注销</el-button>
        </template>
        <template v-else>
          <p class="hint">
            申请后租户立即停止业务写入，并保留 30 天数据导出窗口；到期后关系库、检索库、轨迹和上传文件将物理删除。
          </p>
          <el-button
            type="danger"
            plain
            :loading="requestingCancellation"
            @click="handleRequestCancellation"
          >申请注销租户</el-button>
        </template>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '@/components/common/PageHeader.vue'
import {
  changeTenantPlan,
  exportTenantData,
  getPlanCatalog,
  getRefundThreshold,
  getTenantCancellation,
  requestTenantCancellation,
  revokeTenantCancellation,
  updateRefundThreshold,
} from '@/api/tenant'
import type { PlanCatalog, PlanOption, TenantCancellationStatus } from '@/api/tenant'
import { useTenantStore } from '@/store/modules/tenant'

const threshold = ref(1000)
const saving = ref(false)
const exporting = ref(false)
const catalog = ref<PlanCatalog | null>(null)
const loadingPlans = ref(false)
const upgradingPlan = ref<string | null>(null)
const cancellation = ref<TenantCancellationStatus | null>(null)
const loadingCancellation = ref(false)
const requestingCancellation = ref(false)
const revokingCancellation = ref(false)
const tenantStore = useTenantStore()

const PLAN_RANK: Record<PlanOption['plan_type'], number> = {
  trial: 0,
  basic: 1,
  pro: 2,
  enterprise: 3,
}

onMounted(async () => {
  await loadCancellation()
  if (!cancellation.value?.deletion_pending) {
    await Promise.all([loadPlans(), loadThreshold()])
  }
})

async function loadCancellation() {
  loadingCancellation.value = true
  try {
    const res = await getTenantCancellation()
    cancellation.value = res.data
  } catch (e: any) {
    ElMessage.error(e?.message || '加载注销状态失败')
  } finally {
    loadingCancellation.value = false
  }
}

async function loadPlans() {
  loadingPlans.value = true
  try {
    const res = await getPlanCatalog()
    catalog.value = res.data
  } catch (e: any) {
    ElMessage.error(e?.message || '加载套餐失败')
  } finally {
    loadingPlans.value = false
  }
}

async function loadThreshold() {
  try {
    const res = await getRefundThreshold()
    threshold.value = Number(res.data.refund_approval_threshold) || 1000
  } catch (e: any) {
    ElMessage.error(e?.message || '加载阈值失败')
  }
}

function canUpgrade(plan: PlanOption) {
  if (!catalog.value || catalog.value.pending_plan) return false
  return PLAN_RANK[plan.plan_type] > PLAN_RANK[catalog.value.current_plan]
}

function planButtonText(plan: PlanOption) {
  if (catalog.value?.current_plan === plan.plan_type) return '当前套餐'
  if (catalog.value?.pending_plan === plan.plan_type) return '等待联系'
  if (!canUpgrade(plan)) return '不可降级'
  return plan.negotiable ? '申请企业版' : `升级至${plan.name}`
}

function formatLimit(value: number | null, unit: string) {
  return value === null ? '不限' : `${value.toLocaleString()}${unit}`
}

function formatStorage(value: number | null) {
  if (value === null) return '定制'
  return value >= 1024 ? `${value / 1024}GB` : `${value}MB`
}

function dialogDismissed(error: unknown) {
  return error === 'cancel' || error === 'close'
}

async function handleUpgrade(plan: PlanOption) {
  const message = plan.negotiable
    ? '提交后平台运营将联系您议价，当前套餐在审批前继续生效。确认申请企业版？'
    : `升级至${plan.name}后将立即生效，月费为 ¥${plan.monthly_price}。确认升级？`
  try {
    await ElMessageBox.confirm(message, '套餐升级确认', { type: 'warning' })
    upgradingPlan.value = plan.plan_type
    const res = await changeTenantPlan(plan.plan_type)
    if (res.data.change_status === 'pending_negotiation') {
      ElMessage.success('企业版申请已提交，平台运营将与您联系')
    } else {
      ElMessage.success(`已升级至${plan.name}`)
    }
    await loadPlans()
    tenantStore.plan_type = res.data.current_plan
    tenantStore.pending_plan_type = res.data.pending_plan
  } catch (e: any) {
    if (!dialogDismissed(e)) ElMessage.error(e?.message || '套餐升级失败')
  } finally {
    upgradingPlan.value = null
  }
}

async function handleSave() {
  saving.value = true
  try {
    await updateRefundThreshold(threshold.value)
    ElMessage.success('退款审批阈值已更新')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleExport() {
  exporting.value = true
  try {
    const res = await exportTenantData()
    const blob = new Blob([res.data], { type: 'application/zip' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'zhihub-tenant-export.zip'
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('导出已开始下载')
  } catch (e: any) {
    ElMessage.error(e?.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

function formatDate(value: string | null) {
  if (!value) return '待确认'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

async function handleRequestCancellation() {
  try {
    await ElMessageBox.confirm(
      '申请后租户将立即停止业务写入，仅保留 30 天导出和撤销入口；到期后所有租户数据将不可恢复地物理删除。确认继续？',
      '高风险操作确认',
      { type: 'error', confirmButtonText: '确认申请注销', cancelButtonText: '取消' },
    )
    requestingCancellation.value = true
    const res = await requestTenantCancellation()
    cancellation.value = res.data
    tenantStore.status = res.data.status
    ElMessage.success('注销申请已提交，请在截止日前完成数据导出')
  } catch (e: any) {
    if (!dialogDismissed(e)) ElMessage.error(e?.message || '申请注销失败')
  } finally {
    requestingCancellation.value = false
  }
}

async function handleRevokeCancellation() {
  try {
    await ElMessageBox.confirm('撤销后将恢复申请注销前的租户状态。确认撤销？', '撤销注销', {
      type: 'warning',
    })
    revokingCancellation.value = true
    const res = await revokeTenantCancellation()
    cancellation.value = res.data
    tenantStore.status = res.data.status
    await Promise.all([loadPlans(), loadThreshold()])
    ElMessage.success('注销申请已撤销')
  } catch (e: any) {
    if (!dialogDismissed(e)) ElMessage.error(e?.message || '撤销注销失败')
  } finally {
    revokingCancellation.value = false
  }
}
</script>

<style scoped>
.settings-card { max-width: 1080px; margin-bottom: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.plan-section { min-height: 280px; }
.plan-grid { display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 14px; }
.plan-card { border: 1px solid #ebeef5; border-radius: 10px; padding: 18px; background: #fff; }
.plan-card.current { border-color: #67c23a; box-shadow: 0 0 0 1px #67c23a inset; }
.plan-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.plan-price { color: #303133; font-size: 22px; font-weight: 700; margin: 18px 0 12px; }
.plan-features { color: #606266; line-height: 2; margin: 0 0 16px; padding-left: 18px; }
.hint { color: #909399; font-size: 13px; margin: 0 0 12px; }
.cancellation-card { border-color: #fbc4c4; }
.danger-title { color: #f56c6c; font-size: 16px; font-weight: 700; margin: 0 0 8px; }
@media (max-width: 900px) { .plan-grid { grid-template-columns: repeat(2, minmax(180px, 1fr)); } }
@media (max-width: 520px) { .plan-grid { grid-template-columns: 1fr; } }
</style>
