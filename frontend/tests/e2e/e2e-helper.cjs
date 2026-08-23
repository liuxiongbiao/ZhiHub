const path = require('path');
const fs = require('fs');
const { execFileSync } = require('child_process');
// e2e-helper.cjs — UI 测试共享工具（Playwright + 系统 Chrome）
const { chromium } = require('playwright');

const BASE = 'http://localhost:5173';
const API = 'http://localhost:8000/api/v1';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BACKEND_DIR = path.resolve(__dirname, '..', '..', '..', 'backend');

async function launch() {
  return chromium.launch({ executablePath: CHROME, headless: true });
}

async function newPage(browser) {
  const page = await browser.newPage();
  page.errors = [];
  page.on('console', (m) => { if (m.type() === 'error') page.errors.push(m.text()); });
  page.on('pageerror', (e) => page.errors.push(String(e)));
  return page;
}

function buildApiHeaders(token) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
    headers['X-CSRF-Token'] = token;
  }
  return headers;
}

async function apiJson(path, { method = 'GET', body, token, headers: extraHeaders } = {}) {
  const headers = { ...buildApiHeaders(token), ...(extraHeaders || {}) };
  const res = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-json */ }
  return { status: res.status, data };
}

function randomPhone() {
  return `139${String(Math.floor(10000000 + Math.random() * 89999999)).slice(0, 8)}`;
}

// 为指定租户预置 E2E 数据（工单/订单/退款审批/账单/会话/快捷回复），保证测试确定性
function seedE2E(tenantId) {
  const py = path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe');
  // PII 加密密钥必须与容器一致（deploy/.env），否则种子写入的密文容器无法解密
  const envFile = path.resolve(BACKEND_DIR, '..', 'deploy', '.env');
  let secret = 'change-me';
  try {
    const m = fs.readFileSync(envFile, 'utf8').match(/^APP_SECRET_KEY=(.*)$/m);
    if (m) secret = m[1].trim();
  } catch { /* 默认 dev key */ }
  execFileSync(py, ['e2e-seed.py', String(tenantId)], {
    cwd: BACKEND_DIR,
    stdio: 'pipe',
    env: { ...process.env, APP_SECRET_KEY: secret },
  });
}

// 注册（含短信验证码）→ 自动登录 → 返回 { phone, password, tenantName }
async function registerFlow(page, { phone = randomPhone(), password = 'abc12345', tenantName = '验收测试企业' } = {}) {
  const codeRes = await apiJson('/auth/sms/send', { method: 'POST', body: { phone } });
  const code = codeRes.data?.code;
  await page.goto(`${BASE}/register`, { waitUntil: 'networkidle' });
  // 手机号 tab（默认）
  const inputs = page.locator('input');
  await inputs.nth(0).fill(phone); // 手机号
  await inputs.nth(1).fill(code);  // 验证码
  await inputs.nth(2).fill(password); // 密码
  await inputs.nth(3).fill(tenantName); // 企业名
  await page.locator('button:visible', { hasText: '注册' }).click();
  await page.waitForTimeout(2500);
  return { phone, password, tenantName };
}

// 密码登录 → 返回是否成功
async function passwordLogin(page, { phone, password }) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  const inputs = page.locator('input');
  await inputs.nth(0).fill(phone);
  await inputs.nth(1).fill(password);
  await page.locator('button', { hasText: '登录' }).first().click();
  await page.waitForTimeout(2000);
  return page.url();
}

module.exports = { BASE, API, launch, newPage, buildApiHeaders, apiJson, randomPhone, registerFlow, passwordLogin, seedE2E };
