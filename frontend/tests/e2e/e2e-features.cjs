// e2e-features.cjs — 阶段3：PRD 功能端到端（非 LLM 类，真实浏览器 + 预置数据）
const fs = require('fs');
const path = require('path');
const { launch, newPage, apiJson } = require('./e2e-helper.cjs');

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
  return page.url().startsWith('http://localhost:5173/');
}

async function gotoPage(page, path, wait = 2000) {
  const before = page.errors.length;
  await page.goto(`http://localhost:5173${path}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(wait);
  const errs = page.errors.slice(before).filter((e) => !e.includes('favicon') && !e.includes('429'));
  return { url: page.url(), errors: errs, body: (await page.locator('body').innerText()).trim() };
}

// 限流容错：若 body 为空/无数据且出现 429，等待 32s 重试一次
async function gotoPageRobust(page, path, wait = 2500) {
  let r = await gotoPage(page, path, wait);
  if (r.errors.some((e) => e.includes('429')) || r.body.length < 40) {
    console.log(`   [429/empty] ${path} 等待限流窗口重试...`);
    await page.waitForTimeout(32000);
    r = await gotoPage(page, path, wait);
  }
  return r;
}

(async () => {
  const creds = JSON.parse(fs.readFileSync(path.join(__dirname, 'e2e-cred.json'), 'utf8'));
  const browser = await launch();
  const page = await newPage(browser);
  const okLogin = await login(page, creds);
  record('F-SETUP', okLogin, '功能测试登录');

  // F601 首页：租户信息卡
  {
    const r = await gotoPage(page, '/');
    record('F601', r.url.includes('/') && r.errors.length === 0 && r.body.includes('租户信息'), `url=${r.url} errs=${r.errors.length}`);
  }

  // F604 工单看板：列表渲染 2 张
  {
    const r = await gotoPage(page, '/ticket/list');
    record('F604', r.body.includes('TK-E2E-0001') && r.body.includes('TK-E2E-0002'), `tickets=${r.body.includes('TK-E2E-0001')}/${r.body.includes('TK-E2E-0002')}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F604-tickets.png') });
  }

  // F401 订单列表：paid 订单显示发货按钮
  {
    const r = await gotoPage(page, '/order/list');
    const shipBtn = await page.locator('button:visible', { hasText: '发货' }).count();
    record('F401', r.body.includes('ORD-E2E-0001') && r.body.includes('已支付') && shipBtn >= 1, `shipBtns=${shipBtn}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F401-orders.png') });
  }

  // F402 发货流程：点击发货 → 弹窗 → 填单号 → 确认 → shipped
  {
    await gotoPageRobust(page, '/order/list');
    await page.locator('button:visible', { hasText: '发货' }).first().click();
    await page.waitForTimeout(600);
    const dialog = page.locator('.el-dialog:visible');
    const dlgVisible = await dialog.count() > 0;
    if (dlgVisible) {
      const dlgInput = dialog.locator('input').first();
      await dlgInput.fill('SF-E2E-001');
      await dialog.locator('button:visible', { hasText: '确认发货' }).click();
      await page.waitForTimeout(2500);
    }
    await gotoPageRobust(page, '/order/list');
    const shipped = await page.locator('body').innerText();
    record('F402', dlgVisible && shipped.includes('SF-E2E-001') && shipped.includes('已发货'), `dlg=${dlgVisible} shipped=${shipped.includes('SF-E2E-001')}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F402-shipped.png') });
  }

  // F403 退款审批：pending 记录 + 操作按钮
  {
    const r = await gotoPage(page, '/order/refunds');
    const hasBtn = await page.locator('button:visible', { hasText: /批准|拒绝/ }).count();
    record('F403', r.body.includes('测试退款申请') && hasBtn >= 2, `approvalShown=${r.body.includes('测试退款申请')} btns=${hasBtn}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F403-refunds.png') });
  }

  // F702 账单列表：unpaid + paid
  {
    const r = await gotoPage(page, '/billing/invoices');
    record('F702', r.body.includes('2026-08') && r.body.includes('未支付') && r.body.includes('已支付'), `unpaid=${r.body.includes('未支付')}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F702-invoices.png') });
  }

  // F701 用量概览：quota 数字
  {
    const r = await gotoPage(page, '/billing/usage');
    record('F701', r.body.includes('配额') || r.body.includes('用量') || r.body.includes('使用量'), `body=${r.body.slice(0, 80)}`);
  }

  // F703 支付：unpaid 账单有支付按钮；API pay 返回 pay_url
  {
    await gotoPageRobust(page, '/billing/invoices');
    const payBtn = await page.locator('button:visible', { hasText: '支付' }).count();
    // 从 UI 登录态读取 token，避免重复 login 触发限流
    const token = await page.evaluate(() => {
      try { return JSON.parse(localStorage.getItem('zhihub:user') || '{}').token || ''; } catch { return ''; }
    });
    const invoices = await apiJson('/billing/invoices', { token });
    const unpaid = (invoices.data.items || []).find((i) => i.status === 'unpaid');
    let payResult = null;
    if (unpaid) payResult = await apiJson(`/billing/pay/${unpaid.id}`, { method: 'POST', token });
    record('F703', payBtn >= 1 && payResult && (payResult.status === 200), `uiBtn=${payBtn} api=${payResult ? payResult.status : 'no-unpaid'}`);
    if (payResult && payResult.status === 200) console.log('   pay_url=', (payResult.data.pay_url || '').slice(0, 80));
  }

  // F602 配置向导：状态显示
  {
    const r = await gotoPage(page, '/setup');
    record('F602', r.body.length > 50 && r.errors.length === 0, `bodyLen=${r.body.length}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F602-setup.png') });
  }

  // F603 Widget 预览：聊天组件渲染
  {
    const r = await gotoPage(page, '/widget/preview');
    record('F603', r.body.length > 50 && r.errors.length === 0, `bodyLen=${r.body.length}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F603-widget.png') });
  }

  // F501/F502/F503/F504 坐席工作台：认领 → Trace → 退回；快捷回复
  {
    await gotoPageRobust(page, '/agent/console');
    const sessionId = await page.locator('.el-table__row').count();
    record('F501-list', sessionId >= 1, `waitingRows=${sessionId}`);
    // 认领
    const claimBtn = page.locator('button:visible', { hasText: '认领' }).first();
    const hasClaim = await claimBtn.count() > 0;
    if (hasClaim) {
      await claimBtn.click();
      await page.waitForTimeout(3000);
      await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F501-claimed.png') });
    }
    // Trace 区域
    const traceArea = await page.locator('.trace-area').count();
    const traceText = traceArea > 0 ? await page.locator('.trace-area').innerText() : '';
    record('F503-trace', traceArea > 0, `traceArea=${traceArea} text=${traceText.slice(0, 60)}`);
    // 快捷回复
    const qrBtn = page.locator('button:visible', { hasText: '快捷回复' }).first();
    const hasQr = await qrBtn.count() > 0;
    if (hasQr) {
      await qrBtn.click();
      await page.waitForTimeout(600);
      const qrItems = await page.locator('.qr-item').count();
      if (qrItems > 0) {
        await page.locator('.qr-item').first().click();
        await page.waitForTimeout(400);
      }
      const inputVal = await page.locator('input').last().inputValue();
      record('F504', qrItems >= 1 && inputVal.length > 0, `qrItems=${qrItems} filled=${inputVal.length > 0}`);
    } else {
      record('F504', false, '快捷回复按钮未找到');
    }
    // 退回
    const returnBtn = page.locator('button:visible', { hasText: '退回AI' }).first();
    const hasReturn = await returnBtn.count() > 0;
    if (hasReturn) {
      await returnBtn.click();
      await page.waitForTimeout(2500);
    }
    record('F502-return', hasReturn, `returnBtn=${hasReturn}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F502-return.png') });
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== FEATURES SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
