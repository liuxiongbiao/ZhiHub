<template>
  <div>
    <PageHeader title="工作台" :subtitle="`欢迎回来，${tenantStore.tenant_name || '管理员'}`" />
    <div class="page-container">
      <!-- 013 US5: 系统降级告警横幅（PRD 3.8） -->
      <el-alert v-if="tenantStore.degradation_alert" type="error" :closable="false" show-icon class="banner-alert">
        检索基础设施异常降级中，知识库检索质量可能受影响，请稍后重试。
      </el-alert>
      <!-- 013 US5: 配额预警横幅（PRD 3.9） -->
      <el-alert v-if="tenantStore.warning_banner" type="warning" :closable="false" show-icon class="banner-alert">
        配额预警：当前用量已达 {{ tenantStore.warning_banner }}，请关注用量或升级套餐。
      </el-alert>
      <!-- PRD 3.1/3.8：配置向导完成度提示（未完成红点横幅，点击直达向导） -->
      <el-alert
        v-if="wizardIncomplete"
        type="info"
        :closable="false"
        show-icon
        class="banner-alert wizard-alert"
        @click="router.push('/setup')"
      >
        配置向导未完成（{{ wizardStep }}）—— 点击此处继续初始化
      </el-alert>

      <el-card shadow="never" class="info-card">
        <template #header><span class="section-title">租户信息</span></template>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="企业名称">{{ tenantStore.tenant_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="套餐">
            <el-tag v-if="tenantStore.plan_type" type="primary" effect="light" size="small">{{ PLAN_LABEL[tenantStore.plan_type] || tenantStore.plan_type }}</el-tag>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag v-if="tenantStore.status" :type="tenantStore.status === 'active' ? 'success' : 'info'" size="small">{{ STATUS_LABEL[tenantStore.status] || tenantStore.status }}</el-tag>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item label="当前角色">{{ ROLE_LABEL[tenantStore.current_user?.role ?? ''] || '-' }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <div class="section-title quick-title">快捷入口</div>
      <el-row :gutter="16">
        <el-col v-for="entry in quickEntries" :key="entry.path" :span="6">
          <el-card shadow="hover" class="quick-card" @click="router.push(entry.path)">
            <el-icon class="quick-icon" :style="{ color: entry.color }"><component :is="entry.icon" /></el-icon>
            <div class="quick-text">
              <div class="quick-name">{{ entry.name }}</div>
              <div class="quick-desc">{{ entry.desc }}</div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Collection, Tickets, List, ChatDotRound, User } from '@element-plus/icons-vue'
import { useTenantStore } from '@/store/modules/tenant'
import PageHeader from '@/components/common/PageHeader.vue'
import { getWizardStatus } from '@/api/tenant'

const PLAN_LABEL: Record<string, string> = { trial: '试用', basic: '基础版', pro: '专业版', enterprise: '企业版' }
const STATUS_LABEL: Record<string, string> = { active: '正常', readonly: '只读', closed: '已关闭', pending_review: '待审核' }
const ROLE_LABEL: Record<string, string> = { admin: '管理员', agent: '坐席' }

const tenantStore = useTenantStore()
const router = useRouter()
const wizardIncomplete = ref(false)
const wizardStep = ref('')

const quickEntries = [
  { name: '知识库', desc: '管理与检索', path: '/knowledge/list', icon: Collection, color: '#409eff' },
  { name: '工单看板', desc: '查看与处理', path: '/ticket/list', icon: Tickets, color: '#e6a23c' },
  { name: '订单列表', desc: '订单与支付', path: '/order/list', icon: List, color: '#67c23a' },
  { name: '成员邀请', desc: '邀请坐席', path: '/onboarding/members', icon: User, color: '#f56c6c' },
]

onMounted(async () => {
  if (!tenantStore.tenant_id) {
    try { await tenantStore.fetchHome() } catch (e: any) { if (e?.status !== 401) ElMessage.error(e?.message || '加载失败') }
  }
  // 向导完成度红点（仅 admin，agent 无权限会 403 忽略）
  if (tenantStore.current_user?.role === 'admin') {
    try {
      const res = await getWizardStatus()
      const status = res.data
      wizardIncomplete.value = !status.completed
      if (!status.completed) {
        const idx = (status.steps || []).indexOf(status.current_step)
        wizardStep.value = `第 ${idx >= 0 ? idx + 1 : '?'}/${status.total_steps || '?'} 步`
      }
    } catch { /* 非 admin 或暂不可用时静默 */ }
  }
})
</script>

<style scoped lang="scss">
.banner-alert { margin-bottom: 12px; }
.info-card { margin-bottom: var(--zh-gap-lg); }
.quick-title { margin-bottom: var(--zh-gap); }
.quick-card { cursor: pointer; transition: transform 0.2s, box-shadow 0.2s;
  :deep(.el-card__body) { display: flex; align-items: center; gap: 14px; padding: 20px; } }
.quick-card:hover { transform: translateY(-2px); }
.quick-icon { font-size: 32px; }
.quick-name { font-size: 15px; font-weight: 600; color: var(--zh-text-primary); }
.quick-desc { font-size: 12px; color: var(--zh-text-secondary); margin-top: 4px; }
</style>
