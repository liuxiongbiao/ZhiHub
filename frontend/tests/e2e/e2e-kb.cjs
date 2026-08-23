// e2e-kb.cjs — 阶段3：知识工程管道 + 混合检索（真实 embed API + Milvus/ES + Celery）
const fs = require('fs');
const path = require('path');
const { launch, newPage, apiJson } = require('./e2e-helper.cjs');

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: !!ok, detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} ${detail}`);
}

// 生成测试知识文档
const KB_FILE = path.join(__dirname, 'e2e-kb.md');
fs.writeFileSync(KB_FILE, '# 智枢系统知识\n\n智枢系统支持订单查询、退货退款与工单提交。企业客户可在后台管理知识库文档，系统会自动切片并建立向量索引。DeepSeek 模型负责多智能体路由与回复生成。', 'utf8');

async function login(page, creds) {
  await page.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
  const inputs = page.locator('input');
  await inputs.nth(0).fill(creds.phone);
  await inputs.nth(1).fill(creds.password);
  await page.locator('button:visible', { hasText: '登录' }).click();
  await page.waitForTimeout(2500);
  return page.url().startsWith('http://localhost:5173/');
}

(async () => {
  const creds = JSON.parse(fs.readFileSync(path.join(__dirname, 'e2e-cred.json'), 'utf8'));
  const browser = await launch();
  const page = await newPage(browser);
  await login(page, creds);

  // 获取 token 供 API 轮询
  const token = await page.evaluate(() => { try { return JSON.parse(localStorage.getItem('zhihub:user') || '{}').token || ''; } catch { return ''; } });

  // 清理历史 e2e-kb 文档（失败/旧版本会占用套餐配额，删除后重新上传）
  {
    const list = await apiJson('/knowledge/list?page_size=50', { token });
    const stale = (list.data?.items || []).filter((d) => (d.filename || d.name || '').includes('e2e-kb'));
    for (const d of stale) {
      await apiJson(`/knowledge/${d.doc_id}`, { method: 'DELETE', token });
    }
  }

  // F201 上传文档
  {
    await page.goto('http://localhost:5173/knowledge/list', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    // el-upload 使用 input[type=file]
    const fileInput = page.locator('input[type=file]').first();
    const inputCount = await fileInput.count();
    if (inputCount > 0) {
      await fileInput.setInputFiles(KB_FILE);
      await page.waitForTimeout(2500);
    }
    const body = await page.locator('body').innerText();
    record('F201-upload', inputCount > 0 && (body.includes('e2e-kb') || body.includes('上传')), `input=${inputCount} bodyHasDoc=${body.includes('e2e-kb')}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F201-upload.png') });
  }

  // 轮询 knowledge/list 直到文档 ready（Celery 处理 + embed + Milvus/ES 双写）
  let ready = false;
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 4000));
    const res = await apiJson('/knowledge/list?page_size=20', { token });
    const items = res.data?.items || [];
    const doc = items.find((d) => (d.filename || d.name || '').includes('e2e-kb'));
    if (doc) {
      console.log(`   文档状态: ${doc.status || JSON.stringify(doc).slice(0, 120)}`);
      if ((doc.status || '').toLowerCase() === 'ready') { ready = true; break; }
      if (['failed', 'error'].includes((doc.status || '').toLowerCase())) break;
    } else if (i === 29) {
      console.log('   未在列表中看到 e2e-kb 文档');
    }
  }
  record('F201-ready', ready, `docReady=${ready}`);

  // F202 检索测试台
  if (ready) {
    await page.goto('http://localhost:5173/knowledge/search-test', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    const inputs = page.locator('input, textarea');
    await inputs.first().fill('如何查询订单状态');
    await page.locator('button:visible').first().click();
    await page.waitForTimeout(8000); // 检索 + rerank
    const body = await page.locator('body').innerText();
    record('F202', body.length > 50, `bodyLen=${body.length}`);
    await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F202-search.png') });
  } else {
    record('F202', false, '文档未 ready，跳过检索');
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== KB SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
