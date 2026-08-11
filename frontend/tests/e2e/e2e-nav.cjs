// e2e-nav.cjs — 阶段2：admin 全导航跳转 + 页面渲染检查
const fs = require('fs');
const pathMod = require('path');
const { launch, newPage, registerFlow, passwordLogin } = require('./e2e-helper.cjs');

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: !!ok, detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} ${detail}`);
}

// 侧边栏菜单路径 → 实际菜单文本（Element Plus 不渲染 index 属性，按文本定位）
const SIDEBAR = [
  ['/', '工作台'], ['/widget/preview', 'Widget 预览'], ['/knowledge/list', '知识库'],
  ['/knowledge/search-test', '检索测试台'], ['/ticket/list', '工单看板'],
  ['/order/list', '订单列表'], ['/order/refunds', '退款审批'],
  ['/billing/usage', '用量概览'], ['/billing/invoices', '账单列表'],
  ['/agent-report', 'DeepAgent 报告'], ['/agent/console', '坐席工作台'], ['/setup', '配置向导'],
];

// 直接 URL 访问（sidebar 无入口但路由存在）
const DIRECT = ['/onboarding/members'];

(async () => {
  const browser = await launch();
  const page = await newPage(browser);

  // 登录（复用已有账号，避免重复建租户）
  let creds = null;
  try { creds = JSON.parse(fs.readFileSync(pathMod.join(__dirname, 'e2e-cred.json'), 'utf8')); } catch { /* 无缓存 */ }
  if (creds && creds.phone) {
    await page.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
    const inputs = page.locator('input');
    await inputs.nth(0).fill(creds.phone);
    await inputs.nth(1).fill(creds.password);
    await page.locator('button:visible', { hasText: '登录' }).click();
    await page.waitForTimeout(2000);
    record('NAV-SETUP', page.url().startsWith('http://localhost:5173/'), '复用账号登录');
  } else {
    creds = { phone: '137' + String(Date.now()).slice(-8), password: 'abc12345', tenantName: '导航验收' + String(Date.now()).slice(-3) };
    await registerFlow(page, creds);
    fs.writeFileSync(pathMod.join(__dirname, 'e2e-cred.json'), JSON.stringify(creds, null, 2));
    record('NAV-SETUP', page.url().startsWith('http://localhost:5173/'), '注册并登录');
  }

  // 1) 侧边栏逐项点击跳转
  for (const [path, label] of SIDEBAR) {
    await page.goto('http://localhost:5173/', { waitUntil: 'networkidle' });
    const beforeErrors = page.errors.length;
    let clicked = false;
    const item = page.locator('.el-menu-item').filter({ hasText: label }).first();
    try {
      if (await item.isVisible().catch(() => false)) {
        await item.click({ timeout: 5000 });
      } else {
        // 子菜单折叠：先展开父级 sub-menu 标题
        const parentTitle = page.locator('.el-sub-menu').filter({ has: page.locator('.el-menu-item').filter({ hasText: label }) }).locator('.el-sub-menu__title').first();
        await parentTitle.click({ timeout: 5000 });
        await page.waitForTimeout(400);
        await item.click({ timeout: 5000 });
      }
      clicked = true;
    } catch {
      clicked = false;
    }
    await page.waitForTimeout(1800);
    const url = page.url();
    const okUrl = url.includes(path);
    const newErrors = page.errors.slice(beforeErrors).filter((e) => !e.includes('favicon') && !e.includes('429'));
    const bodyText = (await page.locator('body').innerText()).trim();
    record(`P003-${path}`, okUrl && clicked && newErrors.length === 0 && bodyText.length > 0,
      `clicked=${clicked} url=${url} errors=${JSON.stringify(newErrors)} bodyLen=${bodyText.length}`);
    if (!okUrl || !clicked || newErrors.length > 0) {
      await page.screenshot({ path: pathMod.join(__dirname, 'e2e-shots', `NAV-${path.replaceAll('/', '_')}.png`) });
    }
  }

  // 2) 直接 URL 访问（侧边栏无入口）
  for (const path of DIRECT) {
    const beforeErrors = page.errors.length;
    await page.goto(`http://localhost:5173${path}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const url = page.url();
    const newErrors = page.errors.slice(beforeErrors).filter((e) => !e.includes('favicon'));
    const bodyText = (await page.locator('body').innerText()).trim();
    record(`P003d-${path}`, url.includes(path) && newErrors.length === 0 && bodyText.length > 0,
      `url=${url} errors=${newErrors.length} bodyLen=${bodyText.length}`);
    if (!url.includes(path) || newErrors.length > 0) {
      await page.screenshot({ path: pathMod.join(__dirname, 'e2e-shots', `NAV-${path.replaceAll('/', '_')}.png`) });
    }
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== NAV SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
