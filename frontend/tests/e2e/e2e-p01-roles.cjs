// e2e-p01-roles.cjs -  角色入口与后台导航整理验收
// 覆盖：admin/agent 登录默认页、redirect 优先与开放重定向防护、
//       已登录访问 /login 按角色回首页、agent 越权拦截回工作台、AgentLayout 菜单内聚。
// 运行：node tests/e2e/e2e-p01-roles.cjs  (默认打 E2E_BASE 或 http://localhost:5174)
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { launch, newPage, apiJson, randomPhone } = require('./e2e-helper.cjs');

const BASE = process.env.E2E_BASE || 'http://localhost:5174';
const API = 'http://localhost:8000/api/v1';
const SHOTS = path.join(__dirname, 'e2e-shots');

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: !!ok, detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} :: ${detail}`);
}

async function loginByPassword(page, phone, password) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await fillLoginOnCurrentPage(page, phone, password);
  return page.url();
}

// 在当前页（已处于 /login，可能带 ?redirect=）直接填表提交，避免导航丢 query
async function fillLoginOnCurrentPage(page, phone, password) {
  const inputs = page.locator('input');
  await inputs.nth(0).fill(phone);
  await inputs.nth(1).fill(password);
  await page.locator('button:visible', { hasText: '登录' }).click();
  await page.waitForTimeout(2000);
  return page.url();
}

(async () => {
  // ── 准备账号：复用 admin（e2e-cred.json）做管理端场景；
  //    agent 场景注册全新企业版租户（seat_limit=10；手机注册 schema 不收 plan_type，
  //    邮箱注册才支持），激活 → 登录 → 邀请 → accept 造出 agent ──
  const adminCreds = JSON.parse(fs.readFileSync(path.join(__dirname, 'e2e-cred.json'), 'utf8'));
  const adminLogin = await apiJson('/auth/login', { method: 'POST', body: { phone: adminCreds.phone, password: adminCreds.password } });
  const adminToken = adminLogin.data.access_token;
  if (!adminToken) throw new Error('admin login failed: ' + JSON.stringify(adminLogin.data));

  const stamp = String(Date.now()).slice(-6);
  const ownerEmail = `p01owner${stamp}@test.com`;
  const reg = await apiJson('/auth/register/email', { method: 'POST', body: {
    email: ownerEmail, password: 'owner12345', tenant_name: `P01验收${stamp}`, plan_type: 'enterprise',
  } });
  // dev 回环环境 activation_link 回显 → 提取 token 完成激活（生产行为不影响本脚本）
  const activateToken = reg.data.activation_link ? new URL(reg.data.activation_link).searchParams.get('token') : null;
  const activated = await apiJson('/auth/email/activate', { method: 'POST', body: { token: activateToken } });
  const ownerLogin = await apiJson('/auth/login/email', { method: 'POST', body: { email: ownerEmail, password: 'owner12345' } });
  const ownerToken = ownerLogin.data.access_token;

  const agentPhone = randomPhone();
  const agentPassword = 'agent12345';
  const invite = await apiJson('/tenant/invite', { method: 'POST', token: ownerToken, body: { phone: agentPhone } });
  const accepted = await apiJson('/tenant/invite/accept', { method: 'POST', body: { token: invite.data.invite_token, phone: agentPhone, password: agentPassword } });
  const agentReady = accepted.status === 200 && accepted.data.role === 'agent';
  record('P01-SETUP', reg.status === 201 && activated.status === 200 && agentReady,
    `reg=${reg.status} activate=${activated.status} invite=${invite.status} accept=${accepted.status} role=${accepted.data?.role}`);
  if (!agentReady) throw new Error('agent setup failed');

  const browser = await launch();
  const page = await newPage(browser);
  // 自检第 8 项：收集传输层失败与 5xx 响应（4xx 属守卫场景预期，逐条解释）
  const netErrors = [];
  page.on('requestfailed', (r) => netErrors.push(`reqfail ${r.method()} ${r.url()} :: ${r.failure() && r.failure().errorText}`));
  page.on('response', (r) => { if (r.status() >= 500 || r.status() === 404) netErrors.push(`http${r.status()} ${r.url()}`); });

  // ① admin 密码登录 → 默认进入 /
  {
    const url = await loginByPassword(page, adminCreds.phone, adminCreds.password);
    const ok = url === `${BASE}/`;
    record('P01-ADMIN-HOME', ok, `url=${url}`);
    await page.screenshot({ path: path.join(SHOTS, 'p01-admin-home.png'), fullPage: true });
  }

  // ② admin 访问 /login → 按角色回 /
  {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const url = page.url();
    record('P01-ADMIN-REVISIT-LOGIN', url === `${BASE}/`, `url=${url}`);
  }

  // ③ 未登录访问受保护页 → /login?redirect=，admin 登录后尊重 redirect
  {
    await page.evaluate(() => localStorage.clear());
    await page.goto(`${BASE}/order/list`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(800);
    const loginUrl = page.url();
    const hasRedirect = loginUrl.includes('/login?redirect=/order/list');
    // 已在带 redirect 的登录页，直接填表（不重新 goto，避免丢 query）
    const url = await fillLoginOnCurrentPage(page, adminCreds.phone, adminCreds.password);
    record('P01-REDIRECT-RESPECT', hasRedirect && url === `${BASE}/order/list`, `loginUrl=${loginUrl} landed=${url}`);
  }

  // ④ 开放重定向防护（真实浏览器场景）：
  //    进入带恶意 query 的登录页后必须直接在当前页填表提交（loginByPassword 会重新 goto /login 丢掉 query，
  //    之前的写法等于从未测过恶意 redirect），登录后须停留本站并回角色默认首页。
  //    覆盖：//evil.com、https://evil.com、重复 redirect 参数（vue-router 解析为 string[]）。
  for (const [name, query] of [
    ['protocol-relative', 'redirect=//evil.com'],
    ['absolute-https', 'redirect=https://evil.com'],
    ['duplicate-params', 'redirect=/order/list&redirect=//evil.com'],
  ]) {
    await page.evaluate(() => localStorage.clear());
    await page.goto(`${BASE}/login?${query}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(600);
    const url = await fillLoginOnCurrentPage(page, adminCreds.phone, adminCreds.password);
    const ok = url === `${BASE}/` && !url.includes('evil.com');
    record(`P01-OPEN-REDIRECT-GUARD(${name})`, ok, `query=${query} landed=${url}`);
    await page.screenshot({ path: path.join(SHOTS, `p01-open-redirect-${name}.png`), fullPage: true });
  }

  // ⑤ agent 密码登录 → 默认进入 /agent/console（AgentLayout）
  {
    await page.evaluate(() => localStorage.clear());
    const url = await loginByPassword(page, agentPhone, agentPassword);
    const ok = url === `${BASE}/agent/console`;
    record('P01-AGENT-HOME', ok, `url=${url}`);
    await page.screenshot({ path: path.join(SHOTS, 'p01-agent-home.png'), fullPage: true });
  }

  // ⑥ AgentLayout 菜单内聚：全部 /agent/*，点击工单看板不再跳回 AdminLayout
  {
    const menu = page.locator('.el-menu-item');
    const texts = await menu.allInnerTexts();
    await page.locator('.el-menu-item').filter({ hasText: '工单看板' }).first().click();
    await page.waitForTimeout(1500);
    const url = page.url();
    const stayed = url === `${BASE}/agent/tickets`;
    const stillAgentLayout = await page.locator('.agent-layout').count();
    record('P01-AGENT-MENU', stayed && stillAgentLayout > 0,
      `menu=${JSON.stringify(texts)} url=${url} agentLayout=${stillAgentLayout > 0}`);
    await page.screenshot({ path: path.join(SHOTS, 'p01-agent-tickets.png'), fullPage: true });
  }

  // ⑦ agent 越权访问 admin-only 页（/knowledge/list）→ 回 /agent/console?forbidden=1
  {
    await page.goto(`${BASE}/knowledge/list`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const url = page.url();
    const ok = url.startsWith(`${BASE}/agent/console`) && url.includes('forbidden=1');
    record('P01-AGENT-FORBIDDEN', ok, `url=${url}`);
    await page.screenshot({ path: path.join(SHOTS, 'p01-agent-forbidden.png'), fullPage: true });
  }

  // ⑧ agent 访问 /login → 按角色回 /agent/console
  {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const url = page.url();
    record('P01-AGENT-REVISIT-LOGIN', url.startsWith(`${BASE}/agent/console`) && !url.includes('forbidden'), `url=${url}`);
  }

  // ⑧b  收尾三：agent 打开个人中心 → /agent/profile 且保持 AgentLayout（不回 AdminLayout /profile）
  {
    await page.goto(`${BASE}/agent/console`, { waitUntil: 'networkidle' });
    await page.locator('.user-trigger').click();
    await page.locator('.el-dropdown-menu__item').filter({ hasText: '个人中心' }).first().click();
    await page.waitForTimeout(1500);
    const url = page.url();
    const stillAgentLayout = await page.locator('.agent-layout').count();
    const hasProfileContent = await page.locator('text=修改密码').count().catch(() => 0)
      || await page.locator('.el-form').count();
    record('P01-AGENT-PROFILE', url === `${BASE}/agent/profile` && stillAgentLayout > 0 && hasProfileContent > 0,
      `url=${url} agentLayout=${stillAgentLayout > 0}`);
    await page.screenshot({ path: path.join(SHOTS, 'p01-agent-profile.png'), fullPage: true });
  }

  // ⑧c  收尾三：agent 退出登录 → /login，本地登录态被整体清理
  {
    await page.locator('.user-trigger').click();
    await page.locator('.el-dropdown-menu__item').filter({ hasText: '退出登录' }).first().click();
    await page.waitForTimeout(2000);
    const url = page.url();
    const stored = await page.evaluate(() => localStorage.getItem('zhihub:user'));
    record('P01-AGENT-LOGOUT', url === `${BASE}/login` && !stored, `url=${url} storageCleared=${!stored}`);
    await page.screenshot({ path: path.join(SHOTS, 'p01-agent-logout.png'), fullPage: true });
  }

  // ⑧d  收尾三：退出后受保护页重新要求登录（带回跳 redirect）
  {
    await page.goto(`${BASE}/agent/console`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const url = page.url();
    const ok = url.startsWith(`${BASE}/login`) && url.includes('redirect=/agent/console');
    record('P01-AGENT-LOGOUT-GUARD', ok, `url=${url}`);
  }

  // ⑨ 管理员路由兼容：admin 直达 /agent/console 仍可进入（requiresRole 含 admin）
  {
    await page.evaluate(() => localStorage.clear());
    await loginByPassword(page, adminCreds.phone, adminCreds.password);
    await page.goto(`${BASE}/agent/console`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const url = page.url();
    record('P01-ADMIN-AGENT-CONSOLE', url.startsWith(`${BASE}/agent/console`), `url=${url}`);
  }

  await browser.close();
  // 自检第 8 项：console 错误 / 传输失败 / 5xx 输出（空即干净）
  console.log(`\n=== P01 CONSOLE ERRORS (${page.errors.length}) ===`);
  page.errors.slice(0, 20).forEach((e) => console.log('  [console]', e));
  console.log(`=== P01 NETWORK 5xx/FAILED (${netErrors.length}) ===`);
  netErrors.slice(0, 20).forEach((e) => console.log('  [net]', e));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== P01 SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
