<!-- 套餐与规则配置：套餐升级（PRD §3.9）、退款阈值与数据导出，仅管理员。 -->
<template>
  <div>
    <PageHeader title="套餐与规则配置" subtitle="选择套餐、设置退款规则并管理企业数据" />
    <div class="page-container">
      <el-card shadow="never" class="settings-card plan-section">
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

      <el-card shadow="never" class="settings-card">
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
  updateRefundThreshold,
} from '@/api/tenant'
import type { PlanCatalog, PlanOption } from '@/api/tenant'
import { useTenantStore } from '@/store/modules/tenant'

const threshold = ref(1000)
const saving = ref(false)
const exporting = ref(false)
const catalog = ref<PlanCatalog | null>(null)
const loadingPlans = ref(false)
const upgradingPlan = ref<string | null>(null)
const tenantStore = useTenantStore()

const PLAN_RANK: Record<PlanOption['plan_type'], number> = {
  trial: 0,
  basic: 1,
  pro: 2,
  enterprise: 3,
}

onMounted(async () => {
  await Promise.all([loadPlans(), loadThreshold()])
})

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
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e?.message || '套餐升级失败')
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
@media (max-width: 900px) { .plan-grid { grid-template-columns: repeat(2, minmax(180px, 1fr)); } }
@media (max-width: 520px) { .plan-grid { grid-template-columns: 1fr; } }
</style>
