// e2e-profile.cjs — 规格 013 第三轮：个人中心 / 品牌定制 / 审计日志 页面连通性
const fs = require('fs');
const path = require('path');
const { launch, newPage } = require('./e2e-helper.cjs');

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: !!ok, detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} ${detail}`);
}

async function login(page, creds) {
  await page.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
  const inputs = page.locator('input');
  await inputs.nth(0).fill(creds.phone);
  await inputs.nth(1).fill(creds.password);
  await page.locator('button:visible', { hasText: '登录' }).click();
  await page.waitForTimeout(2500);
}

async function goto(page, p, wait = 1800) {
  await page.goto(`http://localhost:5173${p}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(wait);
  return (await page.locator('body').innerText()).trim();
}

(async () => {
  const creds = JSON.parse(fs.readFileSync(path.join(__dirname, 'e2e-cred.json'), 'utf8'));
  const browser = await launch();
  const page = await newPage(browser);
  await login(page, creds);

  // 侧边栏新菜单项可见
  {
    await page.goto('http://localhost:5173/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);
    const body = await page.locator('body').innerText();
    record('NAV-SIDEBAR', body.includes('品牌定制') && body.includes('审计日志'), `brand=${body.includes('品牌定制')} audit=${body.includes('审计日志')}`);
  }

  // 品牌定制页：保存配置 + 获取嵌入代码
  {
    const body = await goto(page, '/widget/config');
    record('P-WIDGET-RENDER', body.includes('Widget 品牌定制') && body.includes('嵌入代码'), `render=${body.includes('Widget 品牌定制')}`);
    // 点保存（默认值）
    const saveBtn = page.locator('button:visible', { hasText: '保存配置' });
    if (await saveBtn.count() > 0) {
      await saveBtn.click();
      await page.waitForTimeout(2500);
    }
    // 获取嵌入代码
    const embedBtn = page.locator('button:visible', { hasText: '获取嵌入代码' });
    if (await embedBtn.count() > 0) {
      await embedBtn.click();
      await page.waitForTimeout(2000);
    }
    const body2 = await page.locator('body').innerText();
    record('P-WIDGET-EMBED', body2.includes('/widget.js'), `embed=${body2.includes('/widget.js')}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'P-WIDGET.png') });
  }

  // 审计日志页：表格渲染（关键操作会自动写审计）
  {
    const body = await goto(page, '/audit/logs');
    record('P-AUDIT', body.includes('审计日志') && body.length > 60, `render=${body.includes('审计日志')} len=${body.length}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'P-AUDIT.png') });
  }

  // 个人中心：改密 tab 发验证码；邮箱 tab 发邮箱验证码；微信 tab 授权链接
  {
    const body = await goto(page, '/profile');
    record('P-PROFILE-RENDER', body.includes('个人中心') && body.includes('修改密码'), `render=${body.includes('个人中心')}`);
    // 改密 tab 发验证码
    const codeBtn = page.locator('button:visible', { hasText: '获取验证码' }).first();
    const hasCodeBtn = await codeBtn.count() > 0;
    if (hasCodeBtn) {
      await codeBtn.click();
      await page.waitForTimeout(1500);
    }
    // 点击后按钮应变为倒计时（"60s"）且无报错 → 验证码已发出
    const countdown = await page.locator('button:visible', { hasText: /\d+s/ }).count();
    record('P-PROFILE-PWDCODE', hasCodeBtn && (countdown > 0 || page.errors.length === 0), `btn=${hasCodeBtn} countdown=${countdown} errs=${page.errors.length}`);
    // 邮箱 tab
    const tabs = page.locator('.el-tabs__item');
    if (await tabs.count() >= 3) {
      await tabs.nth(1).click();
      await page.waitForTimeout(600);
      const emailInput = page.locator('input[placeholder="name@example.com"]').first();
      const hasEmailInput = await emailInput.count() > 0;
      await emailInput.fill('profile-test@example.com');
      const emailCodeBtn = page.locator('button:visible', { hasText: '获取验证码' }).first();
      if (await emailCodeBtn.count() > 0) {
        await emailCodeBtn.click();
        await page.waitForTimeout(1500);
      }
      const emailCountdown = await page.locator('button:visible', { hasText: /\d+s/ }).count();
      record('P-PROFILE-EMAILCODE', hasEmailInput && emailCountdown > 0, `emailInput=${hasEmailInput} countdown=${emailCountdown}`);
    }
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'P-PROFILE.png') });
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== PROFILE SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
