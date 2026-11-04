<!-- frontend/src/views/widget-demo/WidgetPreview.vue -->
<!-- 职责: Widget 预览双模式（ 目标四）
     1) 后台测试台: 登录态 /session 接口 + ChatWindow，供管理员快速验证 Agent 链路
        （注意: 这不是真实嵌入通道，仅后台测试台）
     2) 真实 Widget 通道预览: 粘贴一次性 API Key，注入真实 /widget.js 公共匿名通道，
        与外部客户网站完全同一脚本，杜绝"后台能用、真实 Widget 不能用"的假闭环 -->
<template>
  <div class="widget-preview-page">
    <section class="channel-hero">
      <div>
        <p class="eyebrow">客户入口</p>
        <h1>把服务入口放到客户面前</h1>
        <p>先用后台测试台验证回答，再用真实匿名通道检查客户看到的完整体验。两条链路边界清晰，共用同一套知识与服务流程。</p>
      </div>
      <div class="flow-card">
        <div class="flow-card-head"><span>当前验证路径</span><strong><i></i> 可测试</strong></div>
        <ServiceFlowRail active="knowledge" compact />
      </div>
    </section>

    <div class="page-container widget-preview">
      <div class="mode-summary">
        <div><span class="mode-code mono-data">01</span><span><strong>后台测试台</strong><small>登录态链路 · 快速验证知识回答</small></span></div>
        <div><span class="mode-code mono-data">02</span><span><strong>真实 Widget 通道</strong><small>匿名链路 · 验证客户网站实际体验</small></span></div>
        <span class="security-note">Key 仅停留在当前页面内存</span>
      </div>

      <el-tabs v-model="activeTab" class="channel-tabs">
      <!-- 模式一: 后台测试台（保留，明确标注） -->
      <el-tab-pane label="后台测试台" name="bench">
        <div class="bench-layout">
          <div class="preview-frame surface-card">
            <div class="widget-header">
              <span class="assistant-mark">枢</span>
              <span class="assistant-title"><strong>智枢智能助手</strong><small>后台测试台 · 登录态通道</small></span>
              <el-tag v-if="sessionId" type="success" size="small">已连接</el-tag>
              <el-tag v-else type="info" size="small">未连接</el-tag>
              <el-button link type="primary" size="small" @click="openTicket">提交工单</el-button>
            </div>

            <div v-if="!sessionId" class="start-area">
              <span class="start-icon">AI</span>
              <strong>开始一次内部验证</strong>
              <p>创建测试会话后，可验证知识命中、引用和人工接管链路。</p>
              <el-button type="primary" :loading="creating" @click="handleCreate">开始对话</el-button>
            </div>

            <div v-else class="chat-area">
              <ChatWindow
                ref="chatRef"
                :session-id="sessionId"
                @message-sent="onMessageSent"
                @new-session="handleCreate"
              />
            </div>
          </div>
          <aside class="validation-guide surface-card">
            <p class="eyebrow">验证步骤</p>
            <h2>建议验证 3 件事</h2>
            <ol>
              <li><b>01</b><span><strong>答案是否明确</strong><small>询问退款条件或发货时效</small></span></li>
              <li><b>02</b><span><strong>引用是否可信</strong><small>检查回答引用的知识片段</small></span></li>
              <li><b>03</b><span><strong>转人工是否顺畅</strong><small>输入“转人工”验证接管</small></span></li>
            </ol>
          </aside>
        </div>
      </el-tab-pane>

      <!-- 模式二: 真实 Widget 通道预览 -->
      <el-tab-pane label="真实 Widget 通道" name="real">
        <div data-test="real-widget-section" class="real-widget-section">
          <section class="credential-panel surface-card">
            <p class="eyebrow">真实通道</p>
            <h2>加载客户网站同款入口</h2>
            <p class="muted">
              <template v-if="demoKeyLoaded">已从演示中心安全载入本机演示 Key，无需复制密码或长期保存凭据。</template>
              <template v-else>在「品牌定制」页生成 Widget API Key 后粘贴到下方（Key 只显示一次，本页不保存）。</template>
              点击加载后，右下角会出现真实在线咨询气泡。
            </p>
            <div class="brand-line">
              <span data-test="tenant-theme-swatch" class="theme-swatch" :style="{ background: tenantTheme }" />
              <span><small>当前租户主题</small><strong class="mono-data">{{ tenantTheme }}</strong></span>
              <img v-if="tenantLogo" data-test="tenant-logo-img" class="tenant-logo" :src="tenantLogo" alt="tenant logo" />
            </div>
            <el-form label-position="top" @submit.prevent="loadRealWidget()">
              <el-form-item label="Widget API Key">
                <el-input v-model="realApiKey" data-test="real-api-key-input"
                  placeholder="粘贴在品牌定制页生成的一次性 API Key" show-password />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="realLoading" data-test="load-real-widget-btn"
                  @click="loadRealWidget()">{{ demoKeyLoaded ? '打开真实客户入口' : '加载真实 Widget' }}</el-button>
              </el-form-item>
            </el-form>
            <el-alert v-if="realLoaded" type="success" :closable="false" show-icon
              title="已注入真实 widget.js" description="查看页面右下角的在线咨询气泡；它与外部网站使用同一脚本和匿名通道。" />
          </section>
          <aside class="channel-proof surface-card">
            <p class="eyebrow">这次能证明什么</p>
            <h2>这不是后台模拟器</h2>
            <div><b>01</b><span><strong>匿名身份</strong><small>不依赖后台登录状态</small></span></div>
            <div><b>02</b><span><strong>真实脚本</strong><small>直接注入生产 widget.js</small></span></div>
            <div><b>03</b><span><strong>真实通道</strong><small>使用公共 API 与 WebSocket</small></span></div>
            <p class="proof-tip">加载成功后，请在右下角完成一轮“提问 → AI 回答 → 转人工”。</p>
          </aside>
        </div>
      </el-tab-pane>
      </el-tabs>
    </div>
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
import { getDemoStatus } from '@/api/demo'
import ServiceFlowRail from '@/components/common/ServiceFlowRail.vue'

const sessionId = ref<number | null>(null)
const creating = ref(false)
const chatRef = ref<InstanceType<typeof ChatWindow> | null>(null)
const ticketDialog = ref(false)
const submitting = ref(false)
const ticketForm = ref({ title: '', description: '', priority: 'medium' })

// : 真实 Widget 通道预览状态（API Key 仅存组件内存，不持久化）
const activeTab = ref(new URLSearchParams(window.location.search).get('mode') === 'real' ? 'real' : 'bench')
const realApiKey = ref('')
const demoKeyLoaded = ref(false)
const realLoading = ref(false)
const realLoaded = ref(false)
// : 租户真实品牌（来自 GET /tenant/widget）
const tenantTheme = ref('#1890ff')
const tenantLogo = ref('')
let embedSrc = ''
let manualLoadStarted = false

async function loadEmbedSource() {
  try {
    const res = await getWidgetEmbedCode()
    const match = /src="([^"]+)\/widget\.js"/.exec(res.data.embed_code || '')
    embedSrc = match ? `${match[1]}/widget.js` : '/widget.js'
  } catch { embedSrc = '/widget.js' }
}

async function loadTenantBrand() {
  try {
    const cfg = await getWidgetConfig()
    tenantTheme.value = cfg.data.theme_color || tenantTheme.value
    tenantLogo.value = cfg.data.logo_url || ''
  } catch { /* 品牌信息加载失败不阻塞预览 */ }
}

async function loadStagedWidgetKey() {
  const stagedDemoKey = sessionStorage.getItem('zhihub:demo-widget-key') || ''
  if (stagedDemoKey) {
    realApiKey.value = stagedDemoKey
    demoKeyLoaded.value = true
    sessionStorage.removeItem('zhihub:demo-widget-key')
    return
  }
  if (realApiKey.value.trim() || manualLoadStarted) return
  try {
    const demo = await getDemoStatus()
    if (demo.data.is_demo && demo.data.widget_api_key) {
      realApiKey.value = demo.data.widget_api_key
      demoKeyLoaded.value = true
    }
  } catch { /* 正式租户继续使用一次性 Key */ }
}

onMounted(async () => {
  await loadEmbedSource()
  await loadTenantBrand()
  await loadStagedWidgetKey()
  if (activeTab.value === 'real' && realApiKey.value) loadRealWidget('auto')
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

function createWidgetScript(key: string) {
  const script = document.createElement('script')
  script.src = embedSrc
  script.async = true
  script.setAttribute('data-api-key', key)
  const origin = new URL(embedSrc, window.location.href).origin
  script.setAttribute('data-endpoint', `${origin}/api/v1/widget`)
  script.setAttribute('data-ws-endpoint', `${origin.replace(/^http/, 'ws')}/api/v1/ws`)
  script.setAttribute('data-theme-color', tenantTheme.value)
  return script
}

/** 注入真实 widget.js（与外部网站同一脚本）。
 *  : 成功/失败以 script.onload/onerror 为准，loading 持续到二者之一。 */
function loadRealWidget(source: 'manual' | 'auto' = 'manual') {
  if (source === 'manual') manualLoadStarted = true
  else if (manualLoadStarted) return
  const key = realApiKey.value.trim()
  if (!key) { ElMessage.warning('请先粘贴 API Key'); return }
  realLoading.value = true
  realLoaded.value = false
  destroyOldWidget()
  document.querySelectorAll('script[data-api-key]').forEach((el) => el.remove())
  const script = createWidgetScript(key)
  script.onload = () => {
    realLoading.value = false
    realLoaded.value = true
    ElMessage.success('已加载真实 Widget，请查看页面右下角')
  }
  script.onerror = () => {
    realLoading.value = false
    realLoaded.value = false
    script.remove()
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
.widget-preview-page { min-height: 100%; }
.channel-hero { display: grid; grid-template-columns: minmax(0,1fr) minmax(410px,.75fr); gap: 42px; align-items: center; min-height: 250px; padding: 36px clamp(24px,4vw,58px); color: #fff; background: var(--zh-ink); }
.channel-hero h1 { margin: 9px 0 11px; font-size: clamp(26px,3vw,42px); letter-spacing: -.04em; }.channel-hero > div:first-child > p:last-child { max-width: 650px; color: #a8bdc0; line-height: 1.75; }
.flow-card { padding: 17px; background: rgba(255,255,255,.035); border: 1px solid rgba(255,255,255,.09); border-radius: 17px; }.flow-card-head { display: flex; justify-content: space-between; margin-bottom: 12px; color: #789399; font-size: 10px; }.flow-card-head strong { color: #9fe3c3; }.flow-card-head i { display: inline-block; width: 6px; height: 6px; margin-right: 5px; background: #62d39a; border-radius: 50%; }
.widget-preview { max-width: 1180px; margin: 0 auto; }.mode-summary { display: grid; grid-template-columns: 1fr 1fr auto; gap: 13px; align-items: center; margin-bottom: 14px; padding: 13px 15px; background: rgba(255,254,250,.78); border: 1px solid var(--zh-border-light); border-radius: 13px; }.mode-summary > div { display: flex; gap: 10px; align-items: center; }.mode-summary > div + div { padding-left: 14px; border-left: 1px solid var(--zh-border-light); }.mode-summary > div > span:last-child { display: grid; }.mode-summary strong { font-size: 11px; }.mode-summary small { margin-top: 2px; color: var(--zh-text-secondary); font-size: 9px; }.mode-code { display: grid; place-items: center; width: 30px; height: 30px; color: var(--zh-primary); font-size: 9px; background: var(--zh-primary-bg); border-radius: 9px; }.security-note { color: var(--zh-text-secondary); font-size: 9px; }
.channel-tabs { padding: 8px 20px 22px; background: var(--zh-bg-card); border: 1px solid var(--zh-border-light); border-radius: var(--zh-radius); box-shadow: var(--zh-shadow); }.channel-tabs :deep(.el-tabs__header) { margin-bottom: 20px; }.channel-tabs :deep(.el-tabs__item) { height: 48px; font-weight: 700; }
.bench-layout, .real-widget-section { display: grid; grid-template-columns: minmax(0,1.45fr) minmax(275px,.55fr); gap: 16px; }.preview-frame { min-height: 510px; overflow: hidden; }.widget-header { display: flex; gap: 10px; align-items: center; min-height: 62px; padding: 11px 14px; color: #dce9e9; background: var(--zh-ink-soft); }.assistant-mark { display: grid; place-items: center; width: 34px; height: 34px; color: #062822; font-size: 12px; font-weight: 850; background: var(--zh-signal); border-radius: 11px 11px 11px 3px; }.assistant-title { display: grid; flex: 1; }.assistant-title strong { font-size: 12px; }.assistant-title small { margin-top: 3px; color: #7f9a9e; font-size: 9px; }.widget-header :deep(.el-button.is-link) { color: #8de1d4; }
.start-area { display: flex; flex-direction: column; justify-content: center; align-items: center; min-height: 445px; padding: 30px; color: var(--zh-text-regular); text-align: center; background: radial-gradient(circle at 50% 35%, rgba(24,183,160,.08), transparent 200px), #fffefa; }.start-icon { display: grid; place-items: center; width: 58px; height: 58px; margin-bottom: 15px; color: var(--zh-primary); font-size: 14px; font-weight: 850; background: var(--zh-primary-bg); border: 1px solid #d2e9e3; border-radius: 19px 19px 19px 5px; }.start-area strong { font-size: 16px; }.start-area p { max-width: 360px; margin: 7px 0 19px; color: var(--zh-text-secondary); font-size: 11px; line-height: 1.65; }.chat-area { min-height: 445px; overflow: hidden; }.chat-area :deep(.chat-window) { min-height: 445px; border: none; border-radius: 0; }
.validation-guide, .credential-panel, .channel-proof { padding: 22px; }.validation-guide h2, .credential-panel h2, .channel-proof h2 { margin: 5px 0 16px; font-size: 18px; }.validation-guide ol { display: grid; gap: 4px; list-style: none; }.validation-guide li, .channel-proof > div { display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: center; padding: 13px 4px; border-top: 1px solid var(--zh-border-light); }.validation-guide b, .channel-proof b { color: var(--zh-primary); font-family: "Cascadia Code", monospace; font-size: 10px; }.validation-guide li > span, .channel-proof div > span { display: grid; }.validation-guide strong, .channel-proof strong { font-size: 11px; }.validation-guide small, .channel-proof small { margin-top: 3px; color: var(--zh-text-secondary); font-size: 9px; }
.real-widget-section { padding-bottom: 2px; }.credential-panel { min-height: 430px; }.muted { margin-bottom: 17px; color: var(--zh-text-regular); font-size: 11px; line-height: 1.75; }.brand-line { display: flex; align-items: center; gap: 10px; margin: 0 0 19px; padding: 11px; background: #f4f8f5; border: 1px solid var(--zh-border-light); border-radius: 10px; }.brand-line > span:nth-child(2) { display: grid; flex: 1; }.brand-line small { color: var(--zh-text-secondary); font-size: 8px; }.brand-line strong { margin-top: 2px; font-size: 10px; }.theme-swatch { display: inline-block; width: 27px; height: 27px; border: 3px solid #fff; border-radius: 9px; box-shadow: 0 0 0 1px var(--zh-border); }.tenant-logo { max-width: 90px; height: 26px; object-fit: contain; border-radius: 5px; }.channel-proof { color: #dce8e9; background: var(--zh-ink-soft); border-color: #29444a; }.channel-proof .eyebrow { color: #6ed4c3; }.channel-proof h2 { color: #fff; }.channel-proof > div { border-color: rgba(255,255,255,.08); }.channel-proof small { color: #7f999e; }.channel-proof b { color: #62d7c4; }.proof-tip { margin-top: 18px; padding: 13px; color: #9fb4b7; font-size: 10px; line-height: 1.65; background: rgba(255,255,255,.045); border-radius: 10px; }
@media (max-width: 980px) { .channel-hero { grid-template-columns: 1fr; }.bench-layout, .real-widget-section { grid-template-columns: 1fr; }.mode-summary { grid-template-columns: 1fr 1fr; }.security-note { grid-column: 1 / -1; } }
@media (max-width: 640px) { .channel-hero { padding: 28px 18px; }.mode-summary { grid-template-columns: 1fr; }.mode-summary > div + div { padding: 0; border: 0; }.channel-tabs { padding: 7px 12px 15px; }.widget-header { flex-wrap: wrap; }.assistant-title { min-width: 140px; } }
</style>
