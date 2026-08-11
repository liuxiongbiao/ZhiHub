<!-- frontend/src/views/onboarding/SetupWizard.vue -->
<!-- 职责: 配置向导 - 5 步状态机(basic->knowledge->mcp->team->done)完整实现 -->
<!-- 对接: GET /tenant/wizard 查状态; POST /tenant/wizard/next 推进(后端防跳步,done 再推进 409) -->
<template>
  <div>
    <PageHeader title="配置向导" subtitle="按步骤完成系统初始化配置" />

    <div class="page-container">
      <el-card shadow="never" v-loading="loading">
        <!-- 步骤条 -->
        <el-steps :active="currentIdx" finish-status="success" align-center>
          <el-step
            v-for="step in status?.steps ?? defaultSteps"
            :key="step"
            :title="STEP_META[step]?.label ?? step"
            :description="STEP_META[step]?.desc"
          />
        </el-steps>

        <!-- 完成态 -->
        <el-result
          v-if="status?.completed"
          icon="success"
          title="配置已完成"
          sub-title="系统已可投入使用，可前往工作台开始使用"
          class="wizard-result"
        >
          <template #extra>
            <el-button type="primary" @click="router.push('/')">返回工作台</el-button>
          </template>
        </el-result>

        <!-- 当前步骤详情 -->
        <div v-else-if="status" class="step-detail">
          <div class="step-detail__head">
            <div>
              <div class="section-title">当前步骤：{{ STEP_META[status.current_step]?.label ?? status.current_step }}</div>
              <p class="step-detail__desc">{{ STEP_META[status.current_step]?.desc }}</p>
            </div>
            <el-button
              v-if="status.current_step === 'knowledge'"
              type="primary"
              plain
              @click="router.push('/knowledge/list')"
            >
              去上传文档
            </el-button>
          </div>

          <div class="step-actions">
            <span class="step-tip">完成本步配置后，点击「下一步」推进向导</span>
            <el-button type="primary" :loading="advancing" @click="handleNext">下一步</el-button>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getWizardStatus, advanceWizard, type WizardStatus } from '@/api/tenant'
import PageHeader from '@/components/common/PageHeader.vue'

const router = useRouter()

/** 默认步骤序列（后端未返回时兜底） */
const defaultSteps = ['basic', 'knowledge', 'mcp', 'team', 'done']

/** 步骤元信息：标签 + 描述 */
const STEP_META: Record<string, { label: string; desc: string }> = {
  basic: { label: '基础信息', desc: '租户基础信息（企业名称、套餐）已在注册时配置完成。' },
  knowledge: { label: '知识库', desc: '上传知识库文档，启用 RAG 检索能力。可点击右侧「去上传文档」。' },
  mcp: { label: 'MCP 工具', desc: 'MCP 工具（工单/订单查询）由后端配置，确认工具链就绪后推进。' },
  team: { label: '团队成员', desc: '邀请坐席成员加入团队，开启人工接待能力。' },
  done: { label: '完成', desc: '配置全部完成，系统已可投入使用。' },
}

const status = ref<WizardStatus | null>(null)
const loading = ref(false)
const advancing = ref(false)

/** 当前步骤在步骤序列中的索引 */
const currentIdx = computed(() => {
  if (!status.value) return 0
  return status.value.steps.indexOf(status.value.current_step)
})

/** 拉取向导状态，非 admin 提示并停留 */
async function loadStatus() {
  loading.value = true
  try {
    const res = await getWizardStatus()
    status.value = res.data
  } catch (e: any) {
    // 非 admin 调向导接口会 403，提示并停留
    ElMessage.error(e?.message || '加载向导状态失败')
  } finally {
    loading.value = false
  }
}

/** 推进向导到下一步，done 后再推进则刷新状态 */
async function handleNext() {
  advancing.value = true
  try {
    const res = await advanceWizard()
    status.value = res.data
    ElMessage.success('已推进到下一步')
  } catch (e: any) {
    // done 再推进后端返 409
    if (e?.status === 409) {
      ElMessage.warning('向导已完成')
      await loadStatus()
    } else {
      ElMessage.error(e?.message || '推进失败')
    }
  } finally {
    advancing.value = false
  }
}

onMounted(loadStatus)
</script>

<style scoped lang="scss">
.wizard-result { padding: 32px 0 8px; }

.step-detail {
  margin-top: var(--zh-gap-lg);
  padding-top: var(--zh-gap-lg);
  border-top: 1px solid var(--zh-border-light);
}
.step-detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--zh-gap);
}
.step-detail__desc {
  margin-top: var(--zh-gap-sm);
  color: var(--zh-text-regular);
  line-height: 1.7;
  font-size: 14px;
}
.step-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--zh-gap-lg);
}
.step-tip { color: var(--zh-text-secondary); font-size: 13px; }
</style>
