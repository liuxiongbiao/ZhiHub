// 产品化 UI 真实浏览器验收：系统 Chrome + 真实 Docker 服务 + 演示快速登录。
const fs = require('fs');
const path = require('path');
const { launch, newPage, BASE } = require('./e2e-helper.cjs');

const SHOTS = path.join(__dirname, 'e2e-shots', 'productization');
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: Boolean(ok), detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} :: ${detail}`);
}

async function noHorizontalOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
}

async function waitForText(page, text, timeout = 15000) {
  await page.getByText(text, { exact: false }).first().waitFor({ state: 'visible', timeout });
}

async function visit(page, route, expectedText, shotName) {
  await page.goto(`${BASE}${route}`, { waitUntil: 'networkidle' });
  await waitForText(page, expectedText);
  const cleanWidth = await noHorizontalOverflow(page);
  record(`PAGE-${shotName.toUpperCase()}-VISIBLE`, true, `${route} · ${expectedText}`);
  record(`PAGE-${shotName.toUpperCase()}-NO-OVERFLOW`, cleanWidth, `viewport=${await page.evaluate(() => window.innerWidth)}`);
  await page.screenshot({ path: path.join(SHOTS, `${shotName}.png`), fullPage: true });
}

(async () => {
  const browser = await launch();
  const desktop = await newPage(browser);
  const failedRequests = [];
  desktop.on('response', (response) => {
    if (response.status() >= 500) failedRequests.push(`${response.status()} ${response.url()}`);
  });
  desktop.on('requestfailed', (request) => {
    failedRequests.push(`FAILED ${request.url()} ${(request.failure() || {}).errorText || ''}`);
  });

  try {
    await desktop.setViewportSize({ width: 1440, height: 960 });
    await desktop.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
    await waitForText(desktop, '安全快速登录');
    record('LOGIN-DEMO-PERSONAS', await desktop.locator('.persona-button').count() === 3, 'admin + 2 agents');
    record('LOGIN-NO-PASSWORD-NEEDED', await desktop.getByText('无需填写或保存演示密码').count() > 0, 'safe local capability');
    record('LOGIN-NO-OVERFLOW', await noHorizontalOverflow(desktop), '1440px');
    await desktop.screenshot({ path: path.join(SHOTS, 'login-desktop.png'), fullPage: true });

    await desktop.locator('.persona-button', { hasText: '管理员' }).click();
    await desktop.waitForURL(/\/demo-center/, { timeout: 15000 });
    await waitForText(desktop, '一段对话，看清智枢的价值');
    await desktop.locator('.story-step').first().waitFor({ state: 'visible', timeout: 15000 });
    const sceneCount = await desktop.locator('.story-step').count();
    record('DEMO-THREE-SCENES', sceneCount === 3, `count=${sceneCount} · knowledge → order → handoff`);
    await desktop.getByRole('button', { name: '下一幕' }).click();
    await waitForText(desktop, '查询真实订单');
    await desktop.getByRole('button', { name: '下一幕' }).click();
    await waitForText(desktop, '完整上下文已交给人工');
    record('DEMO-HANDOFF-CTA', await desktop.getByRole('button', { name: '切换到坐席视角' }).count() === 1, 'visible');
    await desktop.screenshot({ path: path.join(SHOTS, 'demo-handoff.png'), fullPage: true });

    await visit(desktop, '/', '今天需要处理', 'home');
    record('HOME-DECISION-SIGNALS', await desktop.locator('.metric-grid .signal-metric').count() >= 4, 'four operational signals');
    record('HOME-GO-LIVE', await desktop.getByText('上线准备度').count() > 0, 'readiness visible');
    const autoRate = (await desktop.locator('.signal-metric').filter({ hasText: '自动解决率' }).locator('.metric-value').innerText()).trim();
    const readiness = (await desktop.locator('.readiness').innerText()).trim();
    record('HOME-LIVE-DATA', autoRate !== '0%' && readiness !== '0%', `auto=${autoRate} · readiness=${readiness}`);

    await visit(desktop, '/knowledge/quality', '建议先做这些', 'knowledge-quality');
    record('KNOWLEDGE-QUALITY-ACTIONS', await desktop.locator('.recommendation').count() > 0, 'next-best actions visible');

    await desktop.goto(`${BASE}/knowledge/search-test?q=${encodeURIComponent('退款审核通过后多久能到账？')}`, { waitUntil: 'domcontentloaded' });
    await desktop.locator('.result-summary').waitFor({ state: 'visible', timeout: 30000 });
    record('SEARCH-BUSINESS-VERDICT', await desktop.getByText(/回答依据|建议补充知识/).count() > 0, 'human-readable verdict');
    record('SEARCH-HYBRID-COMPARISON', await desktop.locator('.comparison-grid .result-panel').count() === 2, 'vector vs hybrid');
    await desktop.screenshot({ path: path.join(SHOTS, 'search-comparison.png'), fullPage: true });

    await visit(desktop, '/analytics/overview', '现在最值得处理的事', 'operations');
    record('OPERATIONS-SIGNALS', await desktop.locator('.signal-board .signal-item').count() >= 5, 'action-oriented KPI strip');

    await visit(desktop, '/setup', '每一步都用真实配置校验', 'setup');
    record('SETUP-REAL-CHECKS', await desktop.locator('.check-card').count() >= 4, 'knowledge, tools, team, channel');

    await visit(desktop, '/ticket/list', '工单看板', 'tickets');
    record('TICKET-STAGES', await desktop.locator('.workflow-card').count() >= 3, 'workflow stages');
    await visit(desktop, '/order/list', '订单列表', 'orders');
    record('ORDER-STAGES', await desktop.locator('.fulfillment-strip > button').count() >= 4, 'fulfilment stages');

    await desktop.goto(`${BASE}/widget/preview?mode=real`, { waitUntil: 'networkidle' });
    await waitForText(desktop, '这不是后台模拟器');
    await desktop.locator('#zhihub-widget-root').waitFor({ state: 'attached', timeout: 15000 });
    record('REAL-WIDGET-INJECTED', await desktop.locator('#zhihub-widget-root').count() === 1, 'real widget.js channel');
    await desktop.screenshot({ path: path.join(SHOTS, 'widget-real.png'), fullPage: true });

    await desktop.evaluate(() => localStorage.clear());
    await desktop.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
    await desktop.locator('.persona-button', { hasText: '一线坐席' }).click();
    await desktop.waitForURL(/\/agent\/console/, { timeout: 15000 });
    await desktop.locator('.session-item').first().waitFor({ state: 'visible', timeout: 15000 });
    const businessSession = desktop.locator('.session-item').filter({ hasText: '订单查询' }).first();
    await (await businessSession.count() ? businessSession : desktop.locator('.session-item').first()).click();
    await waitForText(desktop, '客户业务信息');
    record('AGENT-THREE-PANELS', await desktop.locator('.queue-panel, .conversation-panel, .context-panel').count() === 3, 'queue + conversation + business');
    record('AGENT-BUSINESS-ACTIONS', await desktop.getByRole('button', { name: /创建售后工单/ }).count() === 1, 'ticket action in context');
    await desktop.screenshot({ path: path.join(SHOTS, 'agent-console.png'), fullPage: true });

    const mobile = await newPage(browser);
    const mobileErrors = mobile.errors;
    await mobile.setViewportSize({ width: 390, height: 844 });
    await mobile.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
    await mobile.locator('.persona-button', { hasText: '管理员' }).click();
    await mobile.waitForURL(/\/demo-center/, { timeout: 15000 });
    record('MOBILE-DEMO-NO-OVERFLOW', await noHorizontalOverflow(mobile), '390px');
    await mobile.screenshot({ path: path.join(SHOTS, 'demo-mobile.png'), fullPage: true });
    await mobile.goto(`${BASE}/`, { waitUntil: 'networkidle' });
    await waitForText(mobile, '今天需要处理');
    record('MOBILE-HOME-NO-OVERFLOW', await noHorizontalOverflow(mobile), '390px');
    await mobile.screenshot({ path: path.join(SHOTS, 'home-mobile.png'), fullPage: true });
    record('MOBILE-CONSOLE-CLEAN', mobileErrors.length === 0, mobileErrors.slice(0, 3).join(' | ') || 'clean');
    await mobile.close();

    record('DESKTOP-CONSOLE-CLEAN', desktop.errors.length === 0, desktop.errors.slice(0, 3).join(' | ') || 'clean');
    record('NO-SERVER-OR-NETWORK-FAILURES', failedRequests.length === 0, failedRequests.slice(0, 5).join(' | ') || 'clean');
  } finally {
    await browser.close();
  }

  const failed = results.filter((item) => !item.ok);
  console.log(`\n=== PRODUCTIZATION SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log(failed.map((item) => `${item.id} :: ${item.detail}`).join('\n'));
    process.exitCode = 1;
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
