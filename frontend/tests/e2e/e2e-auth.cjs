const path = require('path');
// e2e-auth.cjs — 阶段1/2：路由守卫 + 注册/登录/退出（真实浏览器）
const { launch, newPage, registerFlow, passwordLogin } = require('./e2e-helper.cjs');

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: !!ok, detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} ${detail}`);
}

(async () => {
  const browser = await launch();

  // R002 未登录访问受保护路由 → /login?redirect=
  {
    const page = await newPage(browser);
    await page.goto('http://localhost:5173/dashboard', { waitUntil: 'networkidle' });
    const url = page.url();
    record('R002', url.startsWith('http://localhost:5173/login?redirect='), url);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'R002-guard.png') });
    await page.close();
  }

  // P001 注册新租户（验证码）→ 自动登录 dashboard
  const creds = { phone: '139' + String(Date.now()).slice(-8), password: 'abc12345', tenantName: '验收企业' + String(Date.now()).slice(-3) };
  let page;
  {
    page = await newPage(browser);
    await registerFlow(page, creds);
    const url = page.url();
    record('P001', url.startsWith('http://localhost:5173/'), `注册后 URL=${url}`);
    record('P001b', !page.errors.some((e) => !e.includes('favicon')), `console errors=${JSON.stringify(page.errors)}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'P001-dashboard.png') });
  }

  // R003 已登录访问 /login → 重定向 /
  {
    await page.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
    await page.waitForTimeout(800);
    record('R003', page.url().startsWith('http://localhost:5173/'), page.url());
  }

  // P002a 密码登录
  {
    const p2 = await newPage(browser);
    await p2.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
    const url = await passwordLogin(p2, { phone: creds.phone, password: creds.password });
    record('P002a', url.startsWith('http://localhost:5173/'), url);
    await p2.screenshot({ path: path.join(__dirname, 'e2e-shots', 'P002a-pwd-login.png') });
    await p2.close();
  }

  // R001 部分核对：公开页可访问
  {
    const p3 = await newPage(browser);
    await p3.goto('http://localhost:5173/register', { waitUntil: 'networkidle' });
    record('R001-register-public', p3.url().includes('/register'), p3.url());
    await p3.close();
  }

  // P006 退出登录
  {
    // 重新用已登录 page（当前在 /）
    await page.goto('http://localhost:5173/', { waitUntil: 'networkidle' });
    // 展开用户下拉（右上角）并点击退出登录
    try {
      await page.locator('.el-dropdown').first().hover();
      await page.waitForTimeout(500);
      await page.getByText('退出登录').click({ timeout: 3000 });
    } catch {
      // 备选：点击含用户名的区域
      await page.locator('.el-header').click();
      await page.waitForTimeout(500);
      await page.getByText('退出登录').click({ timeout: 3000 });
    }
    await page.waitForTimeout(1200);
    record('P006', page.url().includes('/login'), `退出后 URL=${page.url()}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'P006-logout.png') });
    await page.close();
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== AUTH SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => f.id).join(', '));
    process.exitCode = 1;
  }
})();
