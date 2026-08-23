// e2e-fresh.cjs — 规格 013 fresh-eyes 修复项功能级测试
// 覆盖：①微信扫码回调闭环 ②套餐配额矩阵 ③会话计费首轮 ④监控指标接线
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

  // ── ① 微信扫码回调闭环 ──
  // 1a) 未绑定：先发一个有效 state，访问 /login?code=...&state=... → need_bind 提示
  {
    const wa = await apiJson('/auth/wechat/auth?redirect_uri=http%3A%2F%2Flocalhost%3A5173%2Flogin');
    const state = new URL(wa.data.auth_url).searchParams.get('state');
    await page.goto(`http://localhost:5173/login?code=fresh-unbound-001&state=${state}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    const body = await page.locator('body').innerText();
    record('F-WECHAT-UNBOUND', body.includes('尚未绑定') || body.includes('绑定'), `hint=${body.includes('尚未绑定')}`);
  }

  // 1b) 已绑定：注册新用户 → 绑定 mock openid → 回调应登录成功进入首页
  {
    const phone = '136' + String(Date.now()).slice(-8);
    const mockWechatCode = `fresh-bound-${Date.now()}`;
    const codeRes = await apiJson('/auth/sms/send', { method: 'POST', body: { phone } });
    const reg = await apiJson('/auth/register', { method: 'POST', body: { phone, password: 'abc12345', tenant_name: '微信测试', code: codeRes.data.code } });
    const newLogin = await apiJson('/auth/login', { method: 'POST', body: { phone, password: 'abc12345' } });
    // 绑定 openid（mock 模式 code → mock_openid_{code}）
    const bind = await apiJson('/auth/wechat/bind', { method: 'POST', token: newLogin.data.access_token, body: { code: mockWechatCode } });
    // 再次获取有效 state 走回调
    const wa = await apiJson('/auth/wechat/auth?redirect_uri=http%3A%2F%2Flocalhost%3A5173%2Flogin');
    const state = new URL(wa.data.auth_url).searchParams.get('state');
    await page.goto(`http://localhost:5173/login?code=${encodeURIComponent(mockWechatCode)}&state=${state}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);
    const url = page.url();
    const tokenStored = await page.evaluate(() => { try { return !!JSON.parse(localStorage.getItem('zhihub:user') || '{}').token; } catch { return false; } });
    record('F-WECHAT-BOUND', reg.status === 201 && bind.status === 200 && tokenStored,
      `reg=${reg.status} bind=${bind.status} logged=${tokenStored} url=${url}`);
  }

  // ── ② 套餐配额矩阵 ──
  {
    const trial = await apiJson('/billing/quota', { token });
    record('F-QUOTA-TRIAL', trial.data.quota === 100, `trial quota=${trial.data.quota}`);
  }

  // ── ③ 会话计费：活跃阶段不即时计费，统一在 resolved 时结算 ──
  {
    const before = await apiJson('/billing/quota', { token });
    const sess = await apiJson('/session', { method: 'POST', token, body: { initial_message: '' } });
    const sid = sess.data.session_id;
    // 首轮消息（mock/off LLM 降级路径）
    const r1 = await apiJson('/session/message', { method: 'POST', token, body: { session_id: sid, content: '你好' } });
    const mid = await apiJson('/billing/quota', { token });
    // 二轮消息（活跃阶段仍不计数）
    await apiJson('/session/message', { method: 'POST', token, body: { session_id: sid, content: '继续' } });
    const after = await apiJson('/billing/quota', { token });
    const firstInc = mid.data.count - before.data.count;
    const secondInc = after.data.count - mid.data.count;
    record('F-BILLING-SESSION', r1.status === 200 && firstInc === 0 && secondInc === 0, `activeFirstInc=${firstInc} activeSecondInc=${secondInc}`);
  }

  // ── ④ 监控指标接线 ──
  {
    const m = await fetch('http://localhost:8000/metrics', {
      headers: { Authorization: `Bearer ${token}` },
    }).then((r) => r.text());
    const hasMessages = /zhihub_agent_messages_total\{role="assistant"\} [1-9]\d*/.test(m);
    const hasSessions = /zhihub_active_sessions [0-9.]+/.test(m);
    record('F-METRICS', hasMessages && hasSessions, `messages=${hasMessages} sessions=${hasSessions}`);
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== FRESH SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
