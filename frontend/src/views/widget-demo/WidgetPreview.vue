<!-- frontend/src/views/widget-demo/WidgetPreview.vue -->
<!-- 职责: Widget 预览双模式（ 目标四）
     1) 后台测试台: 登录态 /session 接口 + ChatWindow，供管理员快速验证 Agent 链路
        （注意: 这不是真实嵌入通道，仅后台测试台）
     2) 真实 Widget 通道预览: 粘贴一次性 API Key，注入真实 /widget.js 公共匿名通道，
        与外部客户网站完全同一脚本，杜绝"后台能用、真实 Widget 不能用"的假闭环 -->
<template>
  <div class="widget-preview">
    <el-alert type="info" :closable="false" show-icon class="mode-hint"
      title="双模式预览"
      description="下方『后台测试台』走登录态 /session 接口，仅供内部验证；右侧『真实 Widget 通道』注入与外部网站完全相同的 widget.js 匿名通道（API Key 在 品牌定制 页轮换获取）。" />

    <el-tabs v-model="activeTab">
      <!-- 模式一: 后台测试台（保留，明确标注） -->
      <el-tab-pane label="后台测试台" name="bench">
        <div class="widget-header">
          <h3>🤖 智枢 ZhiHub 智能助手（后台测试台 · 登录态通道）</h3>
          <el-tag v-if="sessionId" type="success" size="small">已连接</el-tag>
          <el-tag v-else type="info" size="small">未连接</el-tag>
          <el-button link type="primary" size="small" @click="openTicket">提交工单</el-button>
        </div>

        <div v-if="!sessionId" class="start-area">
          <p>点击下方按钮开始与智能助手对话</p>
          <el-button type="primary" :loading="creating" @click="handleCreate">
            开始对话
          </el-button>
        </div>

        <div v-else class="chat-area">
          <ChatWindow
            ref="chatRef"
            :session-id="sessionId"
            @message-sent="onMessageSent"
            @new-session="handleCreate"
          />
        </div>
      </el-tab-pane>

      <!-- 模式二: 真实 Widget 通道预览 -->
      <el-tab-pane label="真实 Widget 通道" name="real">
        <div data-test="real-widget-section" class="real-widget-section">
          <p class="muted">
            在「品牌定制」页生成 Widget API Key 后粘贴到下方（Key 只显示一次，本页不保存）。
            点击加载后，右下角会出现与外部客户网站完全一致的在线咨询气泡。
          </p>
          <!-- : 展示租户真实品牌（来自 GET /tenant/widget，非固定默认样式） -->
          <p class="brand-line">
            租户主题色
            <span data-test="tenant-theme-swatch" class="theme-swatch" :style="{ background: tenantTheme }" />
            {{ tenantTheme }}
            <template v-if="tenantLogo">
              ；Logo <img data-test="tenant-logo-img" class="tenant-logo" :src="tenantLogo" alt="tenant logo" />
            </template>
          </p>
          <el-form label-position="top" @submit.prevent="loadRealWidget">
            <el-form-item label="Widget API Key">
              <el-input v-model="realApiKey" data-test="real-api-key-input"
                placeholder="粘贴在品牌定制页生成的一次性 API Key" show-password />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="realLoading" data-test="load-real-widget-btn"
                @click="loadRealWidget">加载真实 Widget</el-button>
            </el-form-item>
          </el-form>
          <el-alert v-if="realLoaded" type="success" :closable="false" show-icon
            title="已注入真实 widget.js" description="查看页面右下角的在线咨询气泡--它与外部网站嵌入的是同一份脚本与同一套匿名通道 API。" />
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>

    <!-- : 用户提交工单弹窗 -->
    <el-dialog v-model="ticketDialog" title="提交工单" width="460px">
      <el-form :model="ticketForm" label-width="70px">
        <el-form-item label="标题">
          <el-input v-model="ticketForm.title" placeholder="请简述问题" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="ticketForm.description" type="textarea" :rows="4" placeholder="详细描述您遇到的问题" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="ticketForm.priority" style="width: 140px">
            <el-option label="低" value="low" />
            <el-option label="中" value="medium" />
            <el-option label="高" value="high" />
            <el-option label="紧急" value="urgent" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ticketDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitTicket">提交</el-button>
      </template>
    </el-dialog>

</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { createSession } from '@/api/session'
import { createTicket } from '@/api/ticket'
import ChatWindow from '@/components/chat/ChatWindow.vue'
import { getWidgetEmbedCode, getWidgetConfig } from '@/api/tenant'

const sessionId = ref<number | null>(null)
const creating = ref(false)
const chatRef = ref<InstanceType<typeof ChatWindow> | null>(null)
const ticketDialog = ref(false)
const submitting = ref(false)
const ticketForm = ref({ title: '', description: '', priority: 'medium' })

// : 真实 Widget 通道预览状态（API Key 仅存组件内存，不持久化）
const activeTab = ref('bench')
const realApiKey = ref('')
const realLoading = ref(false)
const realLoaded = ref(false)
// : 租户真实品牌（来自 GET /tenant/widget）
const tenantTheme = ref('#1890ff')
const tenantLogo = ref('')
let embedSrc = ''

onMounted(async () => {
  // 从后端拿绝对地址的 widget.js 地址（生产未配置 PUBLIC_BASE_URL 时回退同源路径）
  try {
    const res = await getWidgetEmbedCode()
    const m = /src="([^"]+)\/widget\.js"/.exec(res.data.embed_code || '')
    embedSrc = m ? `${m[1]}/widget.js` : '/widget.js'
  } catch { embedSrc = '/widget.js' }
  // : 展示租户真实主题与 Logo
  try {
    const cfg = await getWidgetConfig()
    tenantTheme.value = cfg.data.theme_color || tenantTheme.value
    tenantLogo.value = cfg.data.logo_url || ''
  } catch { /* 品牌信息加载失败不阻塞预览 */ }
})

/** 销毁旧 Widget 实例: 优先走 widget.js 的 destroy()（关 WS/清定时器/删 DOM），
 *  无该 API 时回退删除 DOM（旧版本脚本）。 */
function destroyOldWidget() {
  const api = (window as any).ZhiHubWidget
  if (api && typeof api.destroy === 'function') {
    try { api.destroy() } catch { /* 已销毁 */ }
  } else {
    document.querySelectorAll('#zhihub-widget-root').forEach((el) => el.remove())
  }
}

/** 注入真实 widget.js（与外部网站同一脚本）。
 *  : 成功/失败以 script.onload/onerror 为准，loading 持续到二者之一。 */
function loadRealWidget() {
  const key = realApiKey.value.trim()
  if (!key) { ElMessage.warning('请先粘贴 API Key'); return }
  realLoading.value = true
  realLoaded.value = false
  destroyOldWidget()
  document.querySelectorAll('script[data-api-key]').forEach((el) => el.remove())
  const script = document.createElement('script')
  script.src = embedSrc
  script.async = true
  script.setAttribute('data-api-key', key)
  // endpoint/ws 地址从 script src 同源推导（widget.js 内部按 scheme 换算 ws/wss）
  const origin = new URL(embedSrc, window.location.href).origin
  script.setAttribute('data-endpoint', `${origin}/api/v1/widget`)
  script.setAttribute('data-ws-endpoint', `${origin.replace(/^http/, 'ws')}/api/v1/ws`)
  // : 注入租户真实主题色
  script.setAttribute('data-theme-color', tenantTheme.value)
  script.onload = () => {
    realLoading.value = false
    realLoaded.value = true
    ElMessage.success('已加载真实 Widget，请查看页面右下角')
  }
  script.onerror = () => {
    realLoading.value = false
    realLoaded.value = false
    script.remove() // 清理失败脚本
    destroyOldWidget()
    ElMessage.error('widget.js 加载失败，请检查 PUBLIC_BASE_URL 配置与网络')
  }
  document.head.appendChild(script)
}

/** 创建会话，成功后展示聊天窗口 */
async function handleCreate() {
  creating.value = true
  try {
    const res = await createSession()
    sessionId.value = res.data.session_id
    ElMessage.success('会话已创建')
  } catch (e: any) {
    ElMessage.error(e?.message || '创建会话失败')
  } finally {
    creating.value = false
  }
}

/** 消息发送回调（预留埋点/追踪入口） */
function onMessageSent() {
  // 可用于后续埋点/追踪
}

function openTicket() {
  ticketForm.value = { title: '', description: '', priority: 'medium' }
  ticketDialog.value = true
}

async function submitTicket() {
  if (!ticketForm.value.title.trim()) { ElMessage.warning('请输入工单标题'); return }
  submitting.value = true
  try {
    const res = await createTicket({
      title: ticketForm.value.title.trim(),
      description: ticketForm.value.description.trim() || undefined,
      priority: ticketForm.value.priority,
      category: 'user',
    })
    ElMessage.success(`工单已提交：${res.data.ticket_no}`)
    ticketDialog.value = false
  } catch (e: any) {
    ElMessage.error(e?.message || '提交失败')
  } finally { submitting.value = false }
}

</script>

<style scoped>
.widget-preview {
  max-width: 700px;
  margin: 20px auto;
  min-height: 85vh;
  display: flex;
  flex-direction: column;
}
.mode-hint { margin-bottom: 16px; }
.real-widget-section { padding: 8px 4px; }
.muted { color: #909399; font-size: 13px; }
.brand-line { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #606266; margin: 10px 0; }
.theme-swatch { display: inline-block; width: 16px; height: 16px; border-radius: 4px; border: 1px solid #dcdfe6; }
.tenant-logo { height: 18px; vertical-align: middle; border-radius: 4px; }

.widget-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  border-radius: 12px 12px 0 0;
}

.widget-header h3 {
  margin: 0;
  font-size: 16px;
}

.start-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-top: none;
  border-radius: 0 0 12px 12px;
  gap: 16px;
  color: #666;
}

.chat-area {
  flex: 1;
  border: 1px solid #e4e7ed;
  border-top: none;
  border-radius: 0 0 12px 12px;
  overflow: hidden;
}

.chat-area :deep(.chat-window) {
  border: none;
  border-radius: 0;
}
</style>
