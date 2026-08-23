// e2e-closure.cjs — P1 闭环功能验证
// 覆盖：订单创建 / 工单提交 / 通知站内信（API + UI 存在性）
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
}

(async () => {
  const creds = JSON.parse(fs.readFileSync(path.join(__dirname, 'e2e-cred.json'), 'utf8'));
  const loginRes = await apiJson('/auth/login', { method: 'POST', body: { phone: creds.phone, password: creds.password } });
  const token = loginRes.data.access_token;
  const browser = await launch();
  const page = await newPage(browser);
  await login(page, creds);

  // ① 订单创建（API + UI 按钮）
  {
    const orderNo = 'ORD-CL-' + String(Date.now()).slice(-8);
    const created = await apiJson('/order', { method: 'POST', token, body: { order_no: orderNo, amount: 188.5, paid_amount: 188.5, phone: '13800000001' } });
    const listed = await apiJson('/order/list?page_size=100', { token });
    const found = (listed.data.items || []).some((o) => o.order_no === orderNo && o.status === 'created');
    await page.goto('http://localhost:5173/order/list', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const hasBtn = await page.locator('button:visible', { hasText: '新建订单' }).count() > 0;
    record('F-CL-ORDER', created.status === 201 && found && hasBtn, `create=${created.status} listed=${found} btn=${hasBtn}`);
  }

  // ② 工单提交（API + Widget UI 按钮）
  {
    const created = await apiJson('/ticket', { method: 'POST', token, body: { title: '闭环测试工单', description: '验证提交入口', priority: 'high', category: 'user' } });
    const listed = await apiJson('/ticket/list?page_size=50', { token });
    const found = (listed.data.items || []).some((t) => t.ticket_no === created.data.ticket_no);
    await page.goto('http://localhost:5173/widget/preview', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const hasBtn = await page.locator('button:visible', { hasText: '提交工单' }).count() > 0;
    record('F-CL-TICKET', created.status === 201 && found && hasBtn, `create=${created.status} listed=${found} btn=${hasBtn}`);
  }

  // ③ 通知站内信（API 未读数 + UI 铃铛）
  {
    const unread = await apiJson('/notifications/unread-count', { token });
    await page.goto('http://localhost:5173/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const hasBell = await page.locator('.notif-icon').count() > 0;
    record('F-CL-NOTIF', typeof unread.data.count === 'number' && hasBell, `unread=${unread.data.count} bell=${hasBell}`);
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== CLOSURE SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
