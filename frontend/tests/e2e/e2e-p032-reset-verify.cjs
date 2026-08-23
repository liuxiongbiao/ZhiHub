// e2e-p032-reset-verify.cjs -  验收 B：reset 一次后创建新实例，
// 验证旧租户的不可变审计历史（audit_logs + refund_approval_events）逐字段不变。
//
// 流程:
//   1. demo create（幂等补齐）→ admin 登录 → /tenant/home 拿演示 tenant_id
//   2. 经 backend 容器内 python 导出该租户全部审计/退款事件（JSON，逐字段）
//   3. demo reset（精确 tenant_id）→ demo create（全新实例）
//   4. 再次导出旧 tenant_id 的事件，与 reset 前逐字段比对（必须完全一致）
//   5. 报告全库演示实例 active/archived 数量与新旧实例订单/工单计数
// 运行: node tests/e2e/e2e-p032-reset-verify.cjs
const path = require('path');
const { execFileSync } = require('child_process');
const { apiJson } = require('./e2e-helper.cjs');

const ADMIN_PHONE = '13900000000';
const adminPw = process.env.DEMO_E2E_PASSWORD || 'DemoE2E@Passw0rd';

function composeExec(args) {
  const repo = path.resolve(__dirname, '..', '..', '..');
  return execFileSync('docker', ['compose', '-f', path.join(repo, 'deploy', 'docker-compose.yml'),
    'exec', '-T', 'backend', ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
}

function runSeed(args = []) {
  return composeExec(['python', '-m', 'app.demo.demo_seed', 'create',
    '--admin-password', adminPw, '--agent-password', adminPw, '--reset-passwords', ...args]);
}

// 在 backend 容器内用真实 DB 会话导出租户审计事件（逐字段 JSON）
const DUMP_PY = `
import json
from app.core.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.refund_approval import RefundApproval
from app.models.refund_approval_event import RefundApprovalEvent
from app.models.order import Order
from app.models.tenant import Tenant
s = SessionLocal()
tid = int("__TID__")
order_ids = [o.id for o in s.query(Order).filter(Order.tenant_id == tid).all()]
approval_ids = [r.id for r in s.query(RefundApproval).filter(RefundApproval.order_id.in_(order_ids)).all()] if order_ids else []
audit = sorted([{
    "id": a.id, "user_id": a.user_id, "action": a.action,
    "target_type": a.target_type, "target_id": a.target_id,
    "detail": a.detail, "created_at": str(a.created_at),
} for a in s.query(AuditLog).filter(AuditLog.tenant_id == tid).all()], key=lambda x: x["id"])
events = sorted([{
    "id": e.id, "approval_id": e.approval_id, "event_type": e.event_type,
    "actor_id": e.actor_id, "actor_role": e.actor_role,
    "detail": e.detail, "created_at": str(e.created_at),
} for e in s.query(RefundApprovalEvent).filter(RefundApprovalEvent.approval_id.in_(approval_ids)).all()] if approval_ids else [], key=lambda x: x["id"])
counts = {
    "tenants_active": s.query(Tenant).filter(Tenant.is_demo.is_(True), Tenant.status == "active").count(),
    "tenants_archived": s.query(Tenant).filter(Tenant.is_demo.is_(True), Tenant.status != "active").count(),
    "old_orders": s.query(Order).filter(Order.tenant_id == tid).count(),
}
print("@@JSON@@" + json.dumps({"audit": audit, "refund_events": events, "counts": counts}, ensure_ascii=False))
`;

function dumpTenantEvidence(tid) {
  const out = composeExec(['python', '-c', DUMP_PY.replace('__TID__', String(tid))]);
  const marker = '@@JSON@@';
  const line = out.split('\n').find((l) => l.startsWith(marker));
  if (!line) throw new Error(`evidence dump failed for tenant ${tid}: ${out.slice(0, 400)}`);
  return JSON.parse(line.slice(marker.length));
}

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: !!ok, detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} :: ${detail}`);
}

// Docker Desktop 的宿主端口代理在 seed/外部索引清理后偶发关闭空闲连接。
// 只重试只读 GET 与登录；reset、create 等写操作仍由 composeExec 单次执行，
// 退款/工单等业务 POST 也不经过此脚本，避免掩盖非幂等问题。
async function apiJsonStable(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  const retryable = method === 'GET' || path === '/auth/login';
  try {
    return await apiJson(path, opts);
  } catch (error) {
    if (!retryable) throw error;
    await new Promise((resolve) => setTimeout(resolve, 800));
    return apiJson(path, opts);
  }
}

(async () => {
  // 1. 干净基线（幂等 create；若存在 d34 前旧实例需 ——claim-legacy 认领后才能 reset）
  runSeed(['--claim-legacy']);
  const login = await apiJsonStable('/auth/login', { method: 'POST', body: { phone: ADMIN_PHONE, password: adminPw } });
  const adminToken = login.data && login.data.access_token;
  if (!adminToken) throw new Error('demo admin login failed');
  const home = await apiJsonStable('/tenant/home', { token: adminToken });
  const oldTid = home.data && home.data.tenant_id;
  if (!oldTid) throw new Error('cannot resolve demo tenant_id');
  console.log(`[verify] 演示租户 tenant_id=${oldTid}（reset 目标，来自 /tenant/home，必为演示租户）`);

  // 2. reset 前导出旧租户审计证据
  const before = dumpTenantEvidence(oldTid);
  record('P032-EVIDENCE-NONEMPTY', before.audit.length >= 1 && before.refund_events.length >= 2,
    `audit=${before.audit.length} refund_events=${before.refund_events.length}`);

  // 3. reset（精确 tenant_id）→ 新实例 create
  composeExec(['python', '-m', 'app.demo.demo_seed', 'reset', '--tenant-id', String(oldTid), '--yes']);
  runSeed();

  // 4. 旧租户审计证据逐字段不变
  const after = dumpTenantEvidence(oldTid);
  const auditSame = JSON.stringify(before.audit) === JSON.stringify(after.audit);
  const eventsSame = JSON.stringify(before.refund_events) === JSON.stringify(after.refund_events);
  record('P032-AUDIT-LOGS-UNCHANGED', auditSame, `audit ${before.audit.length} -> ${after.audit.length} identical=${auditSame}`);
  record('P032-REFUND-EVENTS-UNCHANGED', eventsSame, `events ${before.refund_events.length} -> ${after.refund_events.length} identical=${eventsSame}`);

  // 5. 新实例与全库计数（真实 DB 计数）
  const login2 = await apiJsonStable('/auth/login', { method: 'POST', body: { phone: ADMIN_PHONE, password: adminPw } });
  const token2 = login2.data.access_token;
  const home2 = await apiJsonStable('/tenant/home', { token: token2 });
  const newTid = home2.data.tenant_id;
  const newOrders = await apiJsonStable('/order/list', { token: token2 });
  const newTickets = await apiJsonStable('/ticket/list', { token: token2 });
  record('P032-NEW-INSTANCE-READY',
    newTid !== oldTid && (newOrders.data.items || []).length === 6 && (newTickets.data.items || []).length === 2,
    `old_tid=${oldTid} new_tid=${newTid} orders=${(newOrders.data.items || []).length} tickets=${(newTickets.data.items || []).length}`);
  record('P032-DEMO-TENANT-COUNTS',
    after.counts.tenants_active === 1 && after.counts.tenants_archived >= 1,
    `active=${after.counts.tenants_active} archived=${after.counts.tenants_archived} old_orders_kept=${after.counts.old_orders}`);

  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== P032-VERIFY SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
