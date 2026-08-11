// e2e-deepagent.cjs — 阶段3：DeepAgent 开放式任务（真实 LLM 3 步规划 + 报告）
const fs = require('fs');
const path = require('path');
const { launch, newPage } = require('./e2e-helper.cjs');

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: !!ok, detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} ${detail}`);
}

(async () => {
  const creds = JSON.parse(fs.readFileSync(path.join(__dirname, 'e2e-cred.json'), 'utf8'));
  const browser = await launch();
  const page = await newPage(browser);
  await page.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
  const inputs = page.locator('input');
  await inputs.nth(0).fill(creds.phone);
  await inputs.nth(1).fill(creds.password);
  await page.locator('button:visible', { hasText: '登录' }).click();
  await page.waitForTimeout(2500);

  await page.goto('http://localhost:5173/agent-report', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  // 输入目标并执行
  const goalInput = page.locator('input').last();
  await goalInput.fill('分析本租户工单处理效率，给出改进建议');
  await page.locator('button:visible', { hasText: '执行' }).click();

  // 等待报告出现（DeepAgent 3 步：统计 → SOP → 报告，真实 LLM 可能 1-3 分钟）
  const deadline = Date.now() + 180000;
  let reportText = '';
  while (Date.now() < deadline) {
    await page.waitForTimeout(3000);
    const report = page.locator('.report-content');
    if (await report.count() > 0) {
      reportText = (await report.innerText()).trim();
      if (reportText.length > 20) break;
    }
    const stepArea = page.locator('.loading-area, .steps');
    if (await stepArea.count() > 0) {
      const t = (await stepArea.first().innerText()).trim();
      if (t) console.log('   执行中:', t.slice(0, 100).replace(/\n/g, ' | '));
    }
  }
  record('F801', reportText.length > 20, `reportLen=${reportText.length}`);
  console.log('   报告片段:', reportText.slice(0, 150).replace(/\n/g, ' '));
  await page.screenshot({ path: path.join(__dirname, 'e2e-shots', 'F801-deepagent.png') });

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== DEEPAGENT SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
