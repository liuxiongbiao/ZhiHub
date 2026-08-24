<template>
  <div class="agent-console">
    <header class="console-head">
      <div><p class="eyebrow">LIVE SERVICE DESK</p><h1>坐席工作台</h1><p>接住 AI 没有把握的问题，并在同一屏完成订单、工单和退款操作。</p></div>
      <div class="head-actions"><div class="online-chip"><i></i><span><strong>当前在线</strong><small>实时接收转人工会话</small></span></div><el-button v-if="isDemo" plain @click="switchToAdmin">返回管理员演示</el-button><el-button @click="loadWaiting">刷新队列</el-button></div>
    </header>

    <section class="console-body">
      <aside class="queue-panel surface-card">
        <div class="panel-title"><div><p class="eyebrow">ACTION QUEUE</p><h2>待接客户</h2></div><span class="queue-count mono-data">{{ waitingSessions.length }}</span></div>
        <div class="queue-filter"><button type="button" :class="{ active: queueFilter === 'all' }" @click="queueFilter = 'all'">全部</button><button type="button" :class="{ active: queueFilter === 'urgent' }" @click="queueFilter = 'urgent'">高风险</button></div>
        <div v-loading="loading" class="session-list">
          <div v-for="(item, index) in visibleSessions" :key="item.session_id" role="button" tabindex="0" class="session-item" :class="{ active: selectedSession?.session_id === item.session_id, urgent: item.sentiment === 'angry' || item.intent === 'sensitive_action' }" @click="selectSession(item)" @keydown.enter.prevent="selectSession(item)" @keydown.space.prevent="selectSession(item)">
            <span class="session-avatar">{{ item.sentiment === 'angry' ? '!' : '客' }}</span>
            <span class="session-copy"><strong>客户会话 #{{ item.session_id }}</strong><small>{{ intentLabel(item.intent) }} · {{ item.message_count }} 条消息</small><em v-if="typingMap[item.session_id]">客户正在输入…</em><em v-else>{{ statusLabel(item.status) }}</em></span>
            <span class="queue-time"><strong class="mono-data">{{ estimateFor(item.session_id, index) }}</strong><small>预计等待</small></span>
            <el-button v-if="item.status === 'waiting_human'" class="claim-button" type="primary" size="small" @click.stop="handleClaim(item)">认领</el-button>
          </div>
          <div v-if="!loading && !visibleSessions.length" class="queue-empty"><span>✓</span><strong>当前没有等待会话</strong><p>新转人工请求会实时出现在这里。</p></div>
        </div>
      </aside>

      <main class="conversation-panel surface-card">
        <div v-if="!selectedSession" class="no-selection"><span>↖</span><h2>先从左侧选择一位客户</h2><p>选中后会同时载入对话、业务上下文和 AI 判断依据。</p></div>
        <template v-else>
          <div class="conversation-head"><div><strong>会话 #{{ selectedSession.session_id }}</strong><span><i :class="selectedSession.status"></i>{{ statusLabel(selectedSession.status) }}</span></div><div><el-button v-if="selectedSession.status === 'waiting_human'" type="primary" @click="handleClaim(selectedSession)">认领并回复</el-button><el-button v-if="selectedSession.status === 'human_handling'" plain type="warning" @click="handleReturn(selectedSession)">交还给 AI</el-button></div></div>
          <div class="chat-area"><ChatWindow :key="selectedSession.session_id" :session-id="selectedSession.session_id" :prefill-text="prefillText" mode="agent" @message-sent="handleMessageSent" /></div>
          <div class="quick-reply-bar"><span>快捷回复</span><button v-for="reply in quickReplies.slice(0,4)" :key="reply.id" type="button" @click="useQuickReply(reply.content)">{{ reply.shortcut || reply.content.slice(0,12) }}</button><el-popover placement="top-start" :width="340" trigger="click"><template #reference><el-button link size="small">更多</el-button></template><div v-loading="qrLoading" class="qr-list"><button v-for="reply in quickReplies" :key="reply.id" type="button" @click="useQuickReply(reply.content)">{{ reply.content }}</button></div></el-popover></div>
        </template>
      </main>

      <aside class="context-panel surface-card">
        <template v-if="selectedSession">
          <el-tabs v-model="sideTab" class="side-tabs">
            <el-tab-pane label="业务处理" name="business">
              <div v-loading="contextLoading" class="business-context">
                <div class="context-title"><p class="eyebrow">BUSINESS CONTEXT</p><h2>客户业务信息</h2><span>仅展示对话中明确提到的业务编号</span></div>
                <div v-if="business?.order" class="business-card order-card"><div class="business-card-head"><span>ORD</span><div><small>订单</small><strong class="mono-data">{{ business.order.order_no }}</strong></div><el-tag size="small" :type="business.order.status === 'shipped' ? 'success' : 'info'">{{ orderStatus(business.order.status) }}</el-tag></div><dl><div><dt>订单金额</dt><dd>¥{{ business.order.amount }}</dd></div><div><dt>已付金额</dt><dd>¥{{ business.order.paid_amount }}</dd></div><div><dt>物流单号</dt><dd class="mono-data">{{ business.order.tracking_no || '尚未发货' }}</dd></div><div><dt>核验尾号</dt><dd class="mono-data">{{ business.detected.phone_last4 || '需客户提供' }}</dd></div></dl></div>
                <div v-else class="context-empty"><span>ORD</span><strong>尚未识别订单</strong><p>请让客户提供订单号和手机号尾号，系统会自动关联。</p></div>

                <div v-if="business?.ticket" class="business-card ticket-card"><div class="business-card-head"><span>TK</span><div><small>关联工单</small><strong class="mono-data">{{ business.ticket.ticket_no }}</strong></div><el-tag size="small">{{ ticketStatus(business.ticket.status) }}</el-tag></div><p>{{ business.ticket.title }}</p></div>
                <div v-if="business?.refunds.length" class="refund-state"><div><strong>售后进度</strong><el-button link type="primary" @click="router.push('/agent/refunds')">查看审批</el-button></div><p v-for="refund in business.refunds" :key="refund.id"><span>{{ refund.reason || '退款申请' }}</span><el-tag size="small" :type="refund.status === 'pending' ? 'warning' : 'success'">{{ refundStatus(refund.status) }}</el-tag></p></div>

                <div class="business-actions"><el-button type="primary" plain @click="openTicketDialog">创建售后工单</el-button><el-button :disabled="!business?.actions.can_request_refund" type="warning" plain @click="openRefundDialog">发起退款申请</el-button><el-button v-if="business?.actions.can_ship" plain @click="handleShip">登记发货</el-button></div>
                <p class="verification-note">资金操作会要求重新输入完整手机号；界面不会复用或暴露数据库中的敏感信息。</p>
              </div>
            </el-tab-pane>
            <el-tab-pane label="AI 判断依据" name="trace"><TraceTimeline :trace="currentTrace" /></el-tab-pane>
          </el-tabs>
        </template>
        <div v-else class="context-placeholder"><span>◎</span><p>业务上下文和 AI 判断依据会在选择客户后出现。</p></div>
      </aside>
    </section>

    <el-dialog v-model="ticketDialog" title="创建售后工单" width="480px"><el-form label-position="top"><el-form-item label="工单标题"><el-input v-model="ticketForm.title" maxlength="200" /></el-form-item><el-form-item label="问题描述"><el-input v-model="ticketForm.description" type="textarea" :rows="4" maxlength="1000" /></el-form-item><el-form-item label="优先级"><el-select v-model="ticketForm.priority" style="width:100%"><el-option label="普通" value="medium" /><el-option label="高" value="high" /><el-option label="紧急" value="urgent" /></el-select></el-form-item></el-form><template #footer><el-button @click="ticketDialog=false">取消</el-button><el-button type="primary" :loading="actionLoading" @click="submitTicket">创建并关联处理</el-button></template></el-dialog>
    <el-dialog v-model="refundDialog" title="代客户发起退款申请" width="480px"><el-alert type="warning" :closable="false" title="资金操作需要双因子核验" description="订单号来自当前对话；完整手机号必须由客户再次提供。" /><el-form label-position="top" class="refund-form"><el-form-item label="订单号"><el-input v-model="refundForm.order_no" readonly /></el-form-item><el-form-item label="客户完整手机号"><el-input v-model="refundForm.phone" maxlength="11" placeholder="请客户重新提供完整手机号" /></el-form-item><el-form-item label="退款金额"><el-input-number v-model="refundForm.amount" :min="0.01" :max="business?.order?.paid_amount || 999999" :precision="2" style="width:100%" /></el-form-item><el-form-item label="退款原因"><el-input v-model="refundForm.reason" type="textarea" :rows="3" maxlength="500" /></el-form-item></el-form><template #footer><el-button @click="refundDialog=false">取消</el-button><el-button type="warning" :loading="actionLoading" @click="submitRefund">完成核验并提交审批</el-button></template></el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getWsToken } from '@/api/auth'
import { getDemoStatus } from '@/api/demo'
import { claimSession, getQuickReplies, getSessionBusinessContext, getSessionList, getTrace, returnToAgent, selectLatestTrace, type SessionBusinessContext } from '@/api/session'
import { requestRefund, shipOrder } from '@/api/order'
import { createTicket } from '@/api/ticket'
import type { Session } from '@/types/session'
import type { AgentTrace } from '@/types/trace'
import ChatWindow from '@/components/chat/ChatWindow.vue'
import TraceTimeline from '@/components/trace/TraceTimeline.vue'
import { WsClient } from '@/utils/ws-client'
import { AUTH_STORAGE_KEY, API_BASE_URL } from '@/constants'
import { useUserStore } from '@/store/modules/user'

const router = useRouter()
const userStore = useUserStore()
const waitingSessions = ref<Session[]>([])
const selectedSession = ref<Session | null>(null)
const loading = ref(false)
const currentTrace = ref<AgentTrace | null>(null)
const business = ref<SessionBusinessContext | null>(null)
const contextLoading = ref(false)
const prefillText = ref('')
const quickReplies = ref<{ id: number; content: string; shortcut?: string | null }[]>([])
const qrLoading = ref(false)
const typingMap = ref<Record<number, boolean>>({})
const waitMap = ref<Record<number, number>>({})
const queueFilter = ref<'all'|'urgent'>('all')
const sideTab = ref('business')
const isDemo = ref(false)
let wsClient: WsClient | null = null
const visibleSessions = computed(() => queueFilter.value === 'urgent' ? waitingSessions.value.filter((item) => item.sentiment === 'angry' || item.intent === 'sensitive_action') : waitingSessions.value)

const ticketDialog = ref(false)
const refundDialog = ref(false)
const actionLoading = ref(false)
const ticketForm = reactive({ title: '', description: '', priority: 'high' })
const refundForm = reactive({ order_no: '', phone: '', amount: 0, reason: '客户申请售后退款' })

function intentLabel(value?: string) { return ({ knowledge_consult: '知识咨询', order_query: '订单问题', ticket_query: '工单问题', sensitive_action: '风险售后', round_handoff: '多轮未解决' } as Record<string,string>)[value || ''] || '待识别诉求' }
function statusLabel(value: string) { return ({ waiting_human: '等待认领', human_handling: '人工处理中', agent_handling: 'AI 处理中', routing: '正在判断' } as Record<string,string>)[value] || value }
function orderStatus(value: string) { return ({ created: '待支付', paid: '已支付', shipped: '已发货', completed: '已完成', refund_requested: '退款待审批', refunding: '退款中', refunded: '已退款' } as Record<string,string>)[value] || value }
function ticketStatus(value: string) { return ({ open: '待处理', in_progress: '处理中', resolved: '已解决', closed: '已关闭' } as Record<string,string>)[value] || value }
function refundStatus(value: string) { return ({ pending: '待审批', approved: '已批准', rejected: '已拒绝', refunding: '退款中', refunded: '已退款', cancelled: '已撤销' } as Record<string,string>)[value] || value }
function estimateFor(id: number, index: number) { return `${waitMap.value[id] ?? Math.max(1, (index + 1) * 2)}m` }

async function connectWs() {
  try {
    const token = (await getWsToken()).data.token
    if (!token) return
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    wsClient = new WsClient(`${proto}//${window.location.host}${API_BASE_URL}/ws/agent?token=${encodeURIComponent(token)}`, async () => (await getWsToken()).data.token)
    wsClient.onMessage((message) => {
      const payload = message.payload
      if (message.type === 'new_session') {
        if (typeof payload.session_id === 'number' && typeof payload.estimated_wait_minutes === 'number') waitMap.value[payload.session_id] = payload.estimated_wait_minutes
        loadWaiting()
      } else if (message.type === 'session_claimed' && typeof payload.session_id === 'number') {
        const raw = localStorage.getItem(AUTH_STORAGE_KEY)
        const currentUserId = raw ? Number(JSON.parse(raw).user_id) : 0
        if (Number(payload.agent_id) !== currentUserId) waitingSessions.value = waitingSessions.value.filter((item) => item.session_id !== payload.session_id)
      } else if (message.type === 'typing' && typeof payload.session_id === 'number') typingMap.value[payload.session_id] = Boolean(payload.is_typing)
    })
    wsClient.connect().catch(() => {})
  } catch { /* WebSocket 失败不阻塞手动刷新 */ }
}

async function loadWaiting() { loading.value = true; try { const response = await getSessionList({ page_size: 50 }); waitingSessions.value = response.data.items.filter((item) => ['waiting_human','human_handling'].includes(item.status)); if (selectedSession.value) selectedSession.value = waitingSessions.value.find((item) => item.session_id === selectedSession.value?.session_id) || selectedSession.value } catch { /* keep last snapshot */ } finally { loading.value = false } }
async function loadContext(sessionId: number) { contextLoading.value = true; try { const [trace, context] = await Promise.allSettled([getTrace(sessionId), getSessionBusinessContext(sessionId)]); currentTrace.value = trace.status === 'fulfilled' ? selectLatestTrace(trace.value.data.traces) : null; business.value = context.status === 'fulfilled' ? context.value.data : null } finally { contextLoading.value = false } }
function selectSession(item: Session) { selectedSession.value = item; prefillText.value = ''; sideTab.value = 'business'; loadContext(item.session_id) }
async function handleClaim(item: Session) { try { await claimSession(item.session_id); ElMessage.success(`已认领会话 #${item.session_id}`); item.status = 'human_handling'; selectedSession.value = item; await loadWaiting(); await loadContext(item.session_id) } catch (error: any) { ElMessage.error(error?.message || '认领失败，可能已被其他坐席接走'); loadWaiting() } }
async function handleReturn(item: Session) { try { await returnToAgent(item.session_id); ElMessage.success('已交还给 AI 继续服务'); selectedSession.value = null; business.value = null; await loadWaiting() } catch (error: any) { ElMessage.error(error?.message || '交还失败') } }
async function loadQuickReplies() { qrLoading.value = true; try { quickReplies.value = (await getQuickReplies()).data || [] } finally { qrLoading.value = false } }
function useQuickReply(content: string) { prefillText.value = ''; setTimeout(() => { prefillText.value = content }, 0) }
function handleMessageSent() { if (selectedSession.value) loadContext(selectedSession.value.session_id) }

function openTicketDialog() { ticketForm.title = business.value?.order ? `订单 ${business.value.order.order_no} 售后处理` : `会话 #${selectedSession.value?.session_id} 客户问题`; ticketForm.description = `来源会话 #${selectedSession.value?.session_id}。${intentLabel(selectedSession.value?.intent)}，请跟进客户诉求。`; ticketDialog.value = true }
async function submitTicket() { if (!ticketForm.title.trim()) { ElMessage.warning('请填写工单标题'); return } actionLoading.value = true; try { const response = await createTicket({ ...ticketForm, category: 'after_sales' }); ElMessage.success(`工单 ${response.data.ticket_no} 已创建`); ticketDialog.value = false; await loadContext(selectedSession.value!.session_id) } catch (error: any) { ElMessage.error(error?.message || '创建工单失败') } finally { actionLoading.value = false } }
function openRefundDialog() { if (!business.value?.order) return; refundForm.order_no = business.value.order.order_no; refundForm.amount = business.value.order.paid_amount; refundForm.phone = ''; refundDialog.value = true }
async function submitRefund() { if (!/^1[3-9]\d{9}$/.test(refundForm.phone)) { ElMessage.warning('请输入客户完整手机号完成核验'); return } actionLoading.value = true; try { await requestRefund({ ...refundForm, client_request_id: `console-${selectedSession.value?.session_id}-${Date.now()}` }); ElMessage.success('退款申请已进入审批'); refundDialog.value = false; await loadContext(selectedSession.value!.session_id) } catch (error: any) { ElMessage.error(error?.message || '退款申请失败') } finally { actionLoading.value = false } }
async function handleShip() { if (!business.value?.order) return; try { const { value } = await ElMessageBox.prompt('请输入物流单号', '登记发货', { inputPattern: /^.{1,100}$/, inputErrorMessage: '物流单号不能为空' }); await shipOrder(business.value.order.id, value); ElMessage.success('发货状态已更新'); await loadContext(selectedSession.value!.session_id) } catch { /* cancelled */ } }
async function switchToAdmin() { try { await userStore.loginDemo('admin'); router.push('/demo-center') } catch (error: any) { ElMessage.error(error?.message || '切换失败') } }

onMounted(async () => { await Promise.allSettled([loadWaiting(), loadQuickReplies(), getDemoStatus().then((response) => { isDemo.value = response.data.is_demo })]); connectWs() })
onUnmounted(() => { wsClient?.close(); wsClient = null })
</script>

<style scoped>
.agent-console { height: 100vh; display: flex; flex-direction: column; overflow: hidden; padding: 18px; background: var(--zh-bg-page); }.console-head { display: flex; justify-content: space-between; gap: 20px; align-items: center; flex: 0 0 auto; padding: 2px 3px 15px; }.console-head h1 { margin: 3px 0; font-size: 25px; letter-spacing: -.035em; }.console-head p:not(.eyebrow) { color: var(--zh-text-secondary); font-size: 10px; }.head-actions { display: flex; gap: 8px; align-items: center; }.online-chip { display: flex; gap: 9px; align-items: center; margin-right: 4px; padding: 7px 11px; background: var(--zh-primary-bg); border-radius: 10px; }.online-chip i { width: 7px; height: 7px; background: var(--zh-signal); border-radius: 50%; box-shadow: 0 0 0 4px rgba(24,183,160,.1); }.online-chip span { display: grid; }.online-chip strong { font-size: 9px; }.online-chip small { color: var(--zh-text-secondary); font-size: 7px; }
.console-body { display: grid; grid-template-columns: 300px minmax(420px,1fr) 330px; gap: 12px; flex: 1; min-height: 0; }.queue-panel, .conversation-panel, .context-panel { min-width: 0; overflow: hidden; }.queue-panel { display: flex; flex-direction: column; padding: 15px 11px; }.panel-title { display: flex; justify-content: space-between; align-items: center; padding: 0 5px; }.panel-title h2 { margin-top: 3px; font-size: 16px; }.queue-count { display: grid; place-items: center; width: 31px; height: 31px; color: var(--zh-primary); font-size: 13px; background: var(--zh-primary-bg); border-radius: 10px; }.queue-filter { display: flex; gap: 5px; margin: 12px 4px 8px; padding: 3px; background: #f0f3f1; border-radius: 8px; }.queue-filter button { flex: 1; padding: 6px; color: var(--zh-text-secondary); font-size: 9px; background: transparent; border: 0; border-radius: 6px; cursor: pointer; }.queue-filter button.active { color: var(--zh-text-primary); font-weight: 700; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.06); }.session-list { flex: 1; overflow-y: auto; }.session-item { position: relative; display: grid; grid-template-columns: auto 1fr auto; gap: 9px; align-items: start; width: 100%; margin-top: 5px; padding: 12px 9px; text-align: left; color: var(--zh-text-primary); background: transparent; border: 1px solid transparent; border-radius: 11px; cursor: pointer; }.session-item:hover { background: #f4f8f5; }.session-item.active { background: var(--zh-primary-bg); border-color: #c6e3dc; }.session-item.urgent { box-shadow: inset 3px 0 0 var(--zh-coral); }.session-avatar { display: grid; place-items: center; width: 31px; height: 31px; color: #fff; font-size: 10px; font-weight: 800; background: #6f8582; border-radius: 10px; }.urgent .session-avatar { background: var(--zh-coral); }.session-copy { display: grid; min-width: 0; }.session-copy strong { font-size: 10px; }.session-copy small { overflow: hidden; margin-top: 3px; color: var(--zh-text-secondary); font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }.session-copy em { margin-top: 5px; color: var(--zh-primary); font-size: 8px; font-style: normal; }.queue-time { display: grid; justify-items: end; }.queue-time strong { color: var(--zh-amber); font-size: 11px; }.queue-time small { color: var(--zh-text-placeholder); font-size: 7px; }.claim-button { grid-column: 2 / 4; width: 100%; margin-top: 2px; }.queue-empty { display: grid; justify-items: center; padding: 60px 15px; color: var(--zh-text-secondary); text-align: center; }.queue-empty span { display: grid; place-items: center; width: 40px; height: 40px; color: var(--zh-primary); background: var(--zh-primary-bg); border-radius: 13px; }.queue-empty strong { margin-top: 10px; font-size: 11px; }.queue-empty p { margin-top: 4px; font-size: 8px; }
.conversation-panel { display: flex; flex-direction: column; }.no-selection { display: grid; place-items: center; align-content: center; height: 100%; color: var(--zh-text-secondary); text-align: center; }.no-selection > span { display: grid; place-items: center; width: 52px; height: 52px; color: var(--zh-primary); font-size: 21px; background: var(--zh-primary-bg); border-radius: 17px; }.no-selection h2 { margin-top: 13px; color: var(--zh-text-primary); font-size: 15px; }.no-selection p { margin-top: 6px; font-size: 9px; }.conversation-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 15px; border-bottom: 1px solid var(--zh-border-light); }.conversation-head > div:first-child { display: grid; }.conversation-head strong { font-size: 12px; }.conversation-head span { display: flex; gap: 5px; align-items: center; margin-top: 3px; color: var(--zh-text-secondary); font-size: 8px; }.conversation-head i { width: 6px; height: 6px; background: var(--zh-amber); border-radius: 50%; }.conversation-head i.human_handling { background: var(--zh-signal); }.chat-area { flex: 1; min-height: 0; }.chat-area :deep(.chat-window) { border: 0; border-radius: 0; }.quick-reply-bar { display: flex; gap: 6px; align-items: center; overflow-x: auto; padding: 9px 12px; border-top: 1px solid var(--zh-border-light); }.quick-reply-bar > span { flex: 0 0 auto; color: var(--zh-text-secondary); font-size: 8px; }.quick-reply-bar > button { flex: 0 0 auto; padding: 5px 8px; color: var(--zh-text-regular); font-size: 8px; background: #f2f5f3; border: 1px solid var(--zh-border-light); border-radius: 7px; cursor: pointer; }.qr-list button { display: block; width: 100%; padding: 8px; text-align: left; background: transparent; border: 0; border-bottom: 1px solid var(--zh-border-light); cursor: pointer; }
.context-panel { padding: 0 15px 15px; }.side-tabs { height: 100%; }.side-tabs :deep(.el-tabs__content) { height: calc(100% - 55px); overflow-y: auto; }.context-title h2 { margin-top: 3px; font-size: 15px; }.context-title > span { display: block; margin-top: 3px; color: var(--zh-text-secondary); font-size: 8px; }.business-card { margin-top: 14px; padding: 12px; background: #f5f8f6; border: 1px solid var(--zh-border-light); border-radius: 11px; }.business-card-head { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; align-items: center; }.business-card-head > span { display: grid; place-items: center; width: 31px; height: 31px; color: #fff; font-size: 8px; font-weight: 800; background: var(--zh-primary); border-radius: 9px; }.business-card-head > div { display: grid; }.business-card-head small { color: var(--zh-text-secondary); font-size: 7px; }.business-card-head strong { font-size: 9px; }.order-card dl { margin-top: 10px; }.order-card dl div { display: flex; justify-content: space-between; padding: 7px 0; border-top: 1px solid #e3eae7; font-size: 8px; }.order-card dt { color: var(--zh-text-secondary); }.ticket-card .business-card-head > span { background: var(--zh-amber); }.ticket-card > p { margin-top: 9px; color: var(--zh-text-regular); font-size: 9px; }.context-empty { display: grid; justify-items: center; margin-top: 14px; padding: 25px 12px; text-align: center; background: #f5f7f5; border-radius: 11px; }.context-empty > span { display: grid; place-items: center; width: 38px; height: 38px; color: var(--zh-text-secondary); font: 800 9px "Cascadia Code",monospace; background: #e5ebe8; border-radius: 11px; }.context-empty strong { margin-top: 9px; font-size: 10px; }.context-empty p { margin-top: 4px; color: var(--zh-text-secondary); font-size: 8px; line-height: 1.5; }.refund-state { margin-top: 12px; }.refund-state > div, .refund-state p { display: flex; justify-content: space-between; align-items: center; padding: 7px 2px; border-bottom: 1px solid var(--zh-border-light); font-size: 8px; }.business-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-top: 14px; }.business-actions .el-button { margin: 0; font-size: 9px; }.business-actions .el-button:last-child:nth-child(3) { grid-column: 1/-1; }.verification-note { margin-top: 10px; padding: 9px; color: var(--zh-text-secondary); font-size: 7px; line-height: 1.6; background: var(--zh-amber-bg); border-radius: 7px; }.context-placeholder { display: grid; place-items: center; align-content: center; height: 100%; color: var(--zh-text-secondary); text-align: center; }.context-placeholder span { font-size: 25px; color: var(--zh-primary); }.context-placeholder p { max-width: 190px; margin-top: 9px; font-size: 9px; line-height: 1.6; }.refund-form { margin-top: 14px; }
@media (max-width: 1250px) { .console-body { grid-template-columns: 280px 1fr; }.context-panel { position: fixed; z-index: 20; right: 18px; top: 145px; bottom: 18px; width: 330px; display: none; } }
@media (max-width: 820px) { .agent-console { height: auto; min-height: 100vh; }.console-head { align-items: flex-start; flex-direction: column; }.console-body { grid-template-columns: 1fr; }.queue-panel { min-height: 350px; }.conversation-panel { min-height: 650px; }.head-actions { flex-wrap: wrap; } }
@media (min-width: 1251px) { .agent-console { height: calc(100vh - 62px); } }
@media (min-width: 821px) and (max-width: 1250px) { .agent-console { height: auto; min-height: calc(100vh - 62px); overflow: visible; }.context-panel { position: static; display: block; grid-column: 1 / -1; width: auto; min-height: 430px; }.queue-panel, .conversation-panel { min-height: 640px; } }
@media (max-width: 820px) { .context-panel { position: static; display: block; width: auto; min-height: 430px; } }
</style>
