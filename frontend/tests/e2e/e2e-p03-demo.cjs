// e2e-p03-demo.cjs - / 电商演示租户完整剧本验收
// 设计:
//   - 开始前 reset→create 建立干净基线；E2E 用固定幂等键，重复运行不累积业务数据
//   - 幂等性通过 API/DB 真实查询证明，不解析 seed 输出
//   - 捕获 Widget session_id，客服按该 session_id 认领（不认领第一条 waiting_human）
//   - 客户/客服页面全部收集 console error / pageerror / requestfailed，非白名单即失败
// 运行: node tests/e2e/e2e-p03-demo.cjs
//   E2E_BASE 默认 http://localhost（当前可用入口），E2E_API 默认 http://localhost:8000
const fs = require('fs');
const path = require('path');
const http = require('http');
const crypto = require('crypto');
const { execFileSync } = require('child_process');
const { launch, newPage, apiJson } = require('./e2e-helper.cjs');

// Docker Desktop 端口代理偶发关闭空闲连接：网络层抖动重试一次。
// : 仅对 GET 重试 —— 非幂等 POST 不得做无条件网络重试
// （带 client_request_id 的 POST 具备服务端幂等，也统一走单次提交 + 幂等键语义）。
async function apiJsonRetry(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  try {
    return await apiJson(path, opts);
  } catch (e) {
    if (method !== 'GET') throw e;
    await new Promise((r) => setTimeout(r, 800));
    return apiJson(path, opts);
  }
}

async function demoLogin(persona, fallbackPhone, fallbackPassword) {
  const quick = await apiJsonRetry('/auth/login/demo', {
    method: 'POST', body: { persona },
  }).catch(() => null);
  if (quick && quick.status === 200 && quick.data && quick.data.access_token) return quick;
  return apiJsonRetry('/auth/login', {
    method: 'POST', body: { phone: fallbackPhone, password: fallbackPassword },
  });
}

const BASE = process.env.E2E_BASE || 'http://localhost';
const API = process.env.E2E_API || 'http://localhost:8000';
const SHOTS = path.join(__dirname, 'e2e-shots');
fs.mkdirSync(SHOTS, { recursive: true });
const CUSTOMER_PORT = 19997;
const CUSTOMER_ORIGIN = `http://127.0.0.1:${CUSTOMER_PORT}`;
// 固定幂等键：重复 E2E 不新增重复退款/工单
const REFUND_CID = 'p03-e2e-refund-0005';
const TICKET_CID = 'p03-e2e-ticket-0001';
const TICKET_TITLE = '演示赔偿跟进（会话转人工）';
// E2E_NO_RESET=1: 跳过开场的 reset→create（验收 A：同一 active tenant 连续运行两次）
const NO_RESET = process.env.E2E_NO_RESET === '1';

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: !!ok, detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} :: ${detail}`);
}

// 非白名单浏览器错误（空列表 = 任何错误都失败；可按明确原因扩展）
const ALLOWED_ERRORS = [];

function collectPageErrors(page, label) {
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`[console] ${m.text()}`); });
  page.on('pageerror', (e) => errors.push(`[pageerror] ${String(e)}`));
  page.on('requestfailed', (r) => {
    const text = `[requestfailed] ${r.url()} :: ${(r.failure() || {}).errorText || ''}`;
    if (!ALLOWED_ERRORS.some((re) => re.test(text))) errors.push(text);
  });
  page.__errors = errors;
  return errors;
}

// 演示身份（demo seed 内固定）
const ADMIN_PHONE = '13900000000';
const AGENT1_PHONE = '13900000001';
// 快速 persona 登录是默认通道；seed 仍需要密码用于兼容性回退。
// 未显式配置时每次运行随机生成，避免在源码中留下通用演示口令。
function randomE2ePassword(prefix) {
  return `${prefix}!9${crypto.randomBytes(15).toString('base64url')}aA`;
}
const sharedPw = process.env.DEMO_E2E_PASSWORD;
const adminPw = process.env.DEMO_E2E_ADMIN_PASSWORD || sharedPw || randomE2ePassword('ZhAdmin');
const agentPw = process.env.DEMO_E2E_AGENT_PASSWORD || sharedPw || randomE2ePassword('ZhAgent');

function composeExec(args) {
  const repo = path.resolve(__dirname, '..', '..', '..');
  return execFileSync('docker', ['compose', '-f', path.join(repo, 'deploy', 'docker-compose.yml'),
    'exec', '-T', 'backend', ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
}

function runSeed(args = []) {
  return composeExec(['python', '-m', 'app.demo.demo_seed', 'create',
    '--admin-password', adminPw, '--agent-password', agentPw, '--reset-passwords', ...args]);
}

(async () => {
  // ── 干净基线：先 seed 认领/补齐（d34 迁移前旧实例需显式 ——claim-legacy 认领），
  //    再归档当前实例（精确 tenant_id），最后 create 全新干净实例。
  //    E2E_NO_RESET=1 时跳过（验收 A：同租户连续第二次运行）。
  //    注意：reset 目标 tenant_id 来自当前登录演示管理员的 /tenant/home，必为演示租户。
  // ──
  if (NO_RESET) {
    runSeed(); // 仅幂等补齐（含 --reset-passwords 保证固定密码可用）
  } else {
    runSeed(['--claim-legacy']);
    const adminLogin0 = await demoLogin('admin', ADMIN_PHONE, adminPw).catch(() => null);
    const adminToken0 = adminLogin0 && adminLogin0.data && adminLogin0.data.access_token;
    if (adminToken0) {
      const home = await apiJsonRetry('/tenant/home', { token: adminToken0 });
      const tid = home.data && home.data.tenant_id;
      if (tid) composeExec(['python', '-m', 'app.demo.demo_seed', 'reset', '--tenant-id', String(tid), '--yes']);
    }
    runSeed();
  }

  // ── admin 登录 → token ──
  const adminLogin = await demoLogin('admin', ADMIN_PHONE, adminPw);
  const adminToken = adminLogin.data.access_token;
  if (!adminToken) throw new Error('demo admin login failed');

  // Widget: 轮换 key + 白名单（供匿名客户剧本）
  const keyResp = await apiJsonRetry('/tenant/widget/api-key', { method: 'POST', token: adminToken });
  const widgetKey = keyResp.data.api_key;
  await apiJsonRetry('/tenant/widget/domains', { method: 'PUT', token: adminToken, body: { domains: ['127.0.0.1', 'localhost'] } });

  // ── 幂等基线：通过 API 真实查询（不解析 seed 输出）──
  const ordersBefore = await apiJsonRetry('/order/list', { token: adminToken });
  const orderNos = (ordersBefore.data.items || []).map((o) => o.order_no);
  const ticketsBefore = await apiJsonRetry('/ticket/list', { token: adminToken });
  const refundsBefore = await apiJsonRetry('/order/refunds/list', { token: adminToken });
  const kbBefore = await apiJsonRetry('/knowledge/list', { token: adminToken });
  const baseline = {
    orders: orderNos.length,
    tickets: (ticketsBefore.data.items || []).length,
    refunds: (refundsBefore.data.items || []).length,
    kb: (kbBefore.data.items || []).length,
  };
  const order0005 = (ordersBefore.data.items || []).find((o) => o.order_no === 'ORD-DEMO-0005');
  const hadE2eRefundBefore = !!order0005
    && (refundsBefore.data.items || []).some((r) => r.order_id === order0005.id);
  const hadE2eTicketBefore = (ticketsBefore.data.items || []).some((t) => t.title === TICKET_TITLE);
  // fresh/reset 模式必须从 2 条预置数据开始；E2E_NO_RESET 模式允许上一遍已经
  // 创建过本剧本唯一的一条退款/工单，但不允许出现第二条或其他未知累积。
  const expectedBaselineRefunds = 2 + (hadE2eRefundBefore ? 1 : 0);
  const expectedBaselineTickets = 2 + (hadE2eTicketBefore ? 1 : 0);
  const baselineOk = ['ORD-DEMO-0001', 'ORD-DEMO-0002', 'ORD-DEMO-0003', 'ORD-DEMO-0004', 'ORD-DEMO-0005', 'ORD-DEMO-0006']
    .every((n) => orderNos.includes(n))
    && (kbBefore.data.items || []).every((d) => d.status === 'ready') && baseline.kb >= 7
    && baseline.tickets === expectedBaselineTickets
    && baseline.refunds === expectedBaselineRefunds
    && (NO_RESET || (!hadE2eRefundBefore && !hadE2eTicketBefore));
  record('P03-BASELINE', baselineOk && baseline.orders === 6,
    JSON.stringify({ ...baseline, hadE2eRefundBefore, hadE2eTicketBefore }));

  // ── 外部客户页（匿名 Widget）──
  const widgetPageHtml = `<!doctype html><html><head><meta charset="utf-8"><title>客户网站</title></head>
<body><h1>智选商城客户页</h1>
<script src="${BASE}/widget.js"
  data-api-key="${widgetKey}"
  data-endpoint="${API}/api/v1/widget"
  data-ws-endpoint="${API.replace(/^http/, 'ws')}/api/v1/ws"
  async></script></body></html>`;
  const customerServer = http.createServer((q, s) => {
    s.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    s.end(widgetPageHtml);
  });
  await new Promise((r) => customerServer.listen(CUSTOMER_PORT, '127.0.0.1', r));

  const browser = await launch();
  const customer = await newPage(browser);
  const agent = await newPage(browser);
  const customerErrors = collectPageErrors(customer, 'customer');
  const agentErrors = collectPageErrors(agent, 'agent');

  // : 从 POST /api/v1/widget/session 的实际响应捕获 session_id，
  // 不再从 waiting 列表取第一条（避免认领错会话）。
  const widgetSessionIds = [];
  customer.on('response', async (resp) => {
    if (resp.request().method() === 'POST' && /\/api\/v1\/widget\/session$/.test(resp.url())) {
      try {
        const body = await resp.json();
        if (body && body.session_id) widgetSessionIds.push(body.session_id);
      } catch { /* non-json */ }
    }
  });

  async function customerAsk(text) {
    await customer.locator('#zhihub-widget-root input').fill(text);
    await customer.locator('#zhihub-widget-root button', { hasText: '发送' }).click();
    await customer.waitForTimeout(20000); // LLM + 检索
  }

  try {
    // 剧本 1: 客户打开匿名 Widget
    await customer.goto(CUSTOMER_ORIGIN, { waitUntil: 'networkidle' });
    await customer.waitForTimeout(1000);
    await customer.locator('#zhihub-widget-root button', { hasText: '在线咨询' }).click();
    await customer.waitForTimeout(400);
    record('P03-SCENE-1-WIDGET-OPEN', await customer.locator('.zhihub-widget-box').count() > 0, 'widget opened');

    // 剧本 2: 七天无理由退货 → AI 回答 + 引用
    await customerAsk('你们支持七天无理由退货吗？退货流程是什么？');
    const refundAnswer = await customer.locator('.zhihub-widget-log', { hasText: '七天无理由退货' }).count();
    const refundSources = await customer.locator('.zhihub-widget-log', { hasText: '引用' }).count();
    record('P03-SCENE-2-KB-ANSWER', refundAnswer > 0 && refundSources > 0,
      `answer=${refundAnswer > 0} sources=${refundSources > 0}`);
    await customer.screenshot({ path: path.join(SHOTS, 'p03-scene2-kb.png'), fullPage: true });

    // 剧本 3: 查询演示订单 —— 只提供订单号 + 手机号后四位 2222（：
    // agent 原生支持后四位，一轮内必须返回金额/状态/物流单号，无补发完整号码兜底）
    await customerAsk('帮我查一下订单 ORD-DEMO-0003，我的手机号后四位是 2222');
    const orderBubbles = await customer.locator('.zhihub-widget-log > div > div', { hasText: '899' }).count();
    const shippedShown = await customer.locator('.zhihub-widget-log', { hasText: '已发货' }).count();
    const trackingShown = await customer.locator('.zhihub-widget-log', { hasText: 'SF1234567890' }).count();
    // 反问话术特征（order_agent 缺手机号时的固定文案），成功路径不应出现
    const askedFullPhone = await customer.locator('.zhihub-widget-log', { hasText: '请提供您在订单中绑定的手机号' }).count();
    record('P03-SCENE-3-ORDER-QUERY',
      orderBubbles > 0 && shippedShown > 0 && trackingShown > 0 && askedFullPhone === 0,
      `amount=${orderBubbles > 0} shipped=${shippedShown > 0} tracking=${trackingShown > 0} askedPhone=${askedFullPhone}`);
    await customer.screenshot({ path: path.join(SHOTS, 'p03-scene3-order.png'), fullPage: true });

    // 剧本 4/7: 特殊赔偿问题（知识库故意无答案）→ 转人工
    await customerAsk('我收到的商品运输中破损了，你们能额外赔偿我 500 元吗？');
    const waitingHint = await customer.locator('.zhihub-widget-status').filter({ hasText: /人工队列|转接人工|预计等待/ }).count();
    record('P03-SCENE-7-HANDOFF', waitingHint > 0, `waitingHint=${waitingHint > 0}`);
    await customer.screenshot({ path: path.join(SHOTS, 'p03-scene7-handoff.png'), fullPage: true });

    // 使用从 widget/session 响应捕获的精确 session_id（：不得取 waiting 列表第一条）
    const widgetSession = widgetSessionIds[widgetSessionIds.length - 1];
    record('P03-CAPTURE-SESSION-ID', !!widgetSession, `session_id=${widgetSession} (captured=${widgetSessionIds.length})`);
    if (!widgetSession) throw new Error('widget session_id not captured from POST /api/v1/widget/session');
    const sessionId = widgetSession;

    // 剧本 9-11: 客服登录 → 按 session_id 认领（不认领第一条）→ 回复 → 客户实时收到
    await agent.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
    const quickAgent = agent.locator('.persona-button', { hasText: '一线坐席' });
    if (await quickAgent.count()) {
      await quickAgent.click();
    } else {
      const aInputs = agent.locator('input');
      await aInputs.nth(0).fill(AGENT1_PHONE);
      await aInputs.nth(1).fill(agentPw);
      await agent.locator('button:visible', { hasText: '登录' }).click();
    }
    await agent.waitForTimeout(2000);
    const queueRow = agent.locator('.session-item').filter({ hasText: `#${sessionId}` }).first();
    await queueRow.waitFor({ timeout: 20000 }).catch(() => {});
    const queueSeen = await queueRow.count();
    record('P03-SCENE-9-AGENT-QUEUE-BY-ID', queueSeen > 0, `rowForSession=${queueSeen > 0}`);
    await queueRow.locator('button', { hasText: '认领' }).click();
    await agent.waitForTimeout(1200);
    const replyText = `赔偿问题已为您登记，客服稍后电话联系您（演示）`;
    const chatInput = agent.locator('.chat-area input, .chat-window input').first();
    await chatInput.fill(replyText);
    await chatInput.press('Enter');
    await agent.waitForTimeout(1500);
    const customerGot = await customer.locator('.zhihub-widget-log', { hasText: replyText.slice(0, 12) }).count();
    record('P03-SCENE-10-11-AGENT-REPLY-REALTIME', customerGot > 0, `got=${customerGot > 0}`);
    await agent.screenshot({ path: path.join(SHOTS, 'p03-agent-claimed.png'), fullPage: true });

    // 剧本 5/12: 客服代客户创建退款 + 工单（固定幂等键，同租户重复提交返回首次结果）
    const agentLogin = await demoLogin('agent1', AGENT1_PHONE, agentPw);
    const agentToken = agentLogin.data.access_token;
    const refundBody = {
      order_no: 'ORD-DEMO-0005', phone: '13911112222', amount: 50.0,
      reason: '客户要求退款（演示剧本）', client_request_id: REFUND_CID,
    };
    const refundCreate = await apiJsonRetry('/order/refunds/request', {
      method: 'POST', token: agentToken, body: refundBody,
    });
    record('P03-SCENE-5-REFUND-CREATED', refundCreate.status === 201,
      `create=${refundCreate.status} cached=${!!(refundCreate.data && refundCreate.data.cached)}`);
    // 同一 active demo tenant 内重复提交相同 client_request_id → 必须返回同一审批
    const refundsBetween = await apiJsonRetry('/order/refunds/list', { token: adminToken });
    const countBetween = (refundsBetween.data.items || []).length;
    const refundReplay = await apiJsonRetry('/order/refunds/request', {
      method: 'POST', token: agentToken, body: refundBody,
    });
    const refundsAfterReplay = await apiJsonRetry('/order/refunds/list', { token: adminToken });
    const countAfterReplay = (refundsAfterReplay.data.items || []).length;
    const sameApproval = refundReplay.status === 201 && refundCreate.data && refundReplay.data
      && refundReplay.data.id === refundCreate.data.id;
    record('P03-IDEMPOTENT-REFUND-REPLAY', sameApproval && countAfterReplay === countBetween,
      `sameId=${sameApproval} count ${countBetween} -> ${countAfterReplay} status=${refundReplay.status}`);

    // 工单幂等：服务端 client_request_id 幂等，直接重复提交两次
    const ticketBody = {
      title: TICKET_TITLE, description: '客户要求额外赔偿，需电话跟进',
      priority: 'medium', category: 'general', client_request_id: TICKET_CID,
    };
    const ticket1 = await apiJsonRetry('/ticket', { method: 'POST', token: agentToken, body: ticketBody });
    const ticket2 = await apiJsonRetry('/ticket', { method: 'POST', token: agentToken, body: ticketBody });
    const ticketsAfter = await apiJsonRetry('/ticket/list', { token: adminToken });
    const matchedTickets = (ticketsAfter.data.items || []).filter((t) => t.title === TICKET_TITLE);
    record('P03-SCENE-12-AGENT-TICKET',
      ticket1.status === 201 && ticket2.status === 201
        && ticket1.data.id === ticket2.data.id && matchedTickets.length === 1,
      `t1=${ticket1.status} t2=${ticket2.status} sameId=${ticket1.data.id === ticket2.data.id} count=${matchedTickets.length}`);

    // 剧本 13: 管理员统计断言同一个 session_id（human_handling）
    const sessionsAfter = await apiJsonRetry('/session/list', { token: adminToken });
    const tracked = (sessionsAfter.data.items || []).find((s) => s.session_id === sessionId);
    record('P03-SCENE-13-SESSION-TRACKED', !!tracked && (tracked.status === 'human_handling' || tracked.status === 'waiting_human'),
      `session=${sessionId} status=${tracked && tracked.status}`);

    // ── 幂等复验：refund 固定幂等键 → 总数不因重复 E2E 累积（相对基线只 +1）──
    const refundsAfter = await apiJsonRetry('/order/refunds/list', { token: adminToken });
    const refundsAfterCount = (refundsAfter.data.items || []).length;
    const expectedRefundsAfter = baseline.refunds + (hadE2eRefundBefore ? 0 : 1);
    record('P03-IDEMPOTENT-REFUNDS', refundsAfterCount === expectedRefundsAfter,
      `baseline=${baseline.refunds} expected=${expectedRefundsAfter} after=${refundsAfterCount}`);
  } finally {
    customerServer.close();
    await browser.close();
  }

  // 浏览器错误：非白名单错误必须失败
  const allErrors = [...customerErrors, ...agentErrors];
  record('P03-BROWSER-ERRORS', allErrors.length === 0, allErrors.slice(0, 5).join(' | ') || 'clean');

  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== P03 SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
