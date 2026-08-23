// e2e-api.cjs — 阶段4：API 连通性补充（CSAT / 留言建单 / WebSocket）
const fs = require('fs');
const path = require('path');
const { apiJson, seedE2E } = require('./e2e-helper.cjs');

const results = [];
function record(id, ok, detail = '') {
  results.push({ id, ok: !!ok, detail });
  console.log(`${ok ? '[PASS]' : '[FAIL]'} ${id} ${detail}`);
}

(async () => {
  const creds = JSON.parse(fs.readFileSync(path.join(__dirname, 'e2e-cred.json'), 'utf8'));
  const login = await apiJson('/auth/login', { method: 'POST', body: { phone: creds.phone, password: creds.password } });
  const token = login.data.access_token;
  // 预置 closed / waiting_human 会话等数据（幂等）
  if (login.data.tenant_id) seedE2E(login.data.tenant_id);

  // 找到 closed 会话与 waiting_human 会话
  const list = await apiJson('/session/list?page_size=50', { token });
  const sessions = list.data.items || [];
  const closedSess = sessions.find((s) => s.status === 'closed');
  const waitingSess = sessions.find((s) => s.status === 'waiting_human');

  // A001 CSAT：closed 会话提交 → 201；重复 → 409
  if (closedSess) {
    const first = await apiJson(`/session/${closedSess.session_id}/csat`, { method: 'POST', token, body: { score: 5, comment: '服务很好' } });
    const second = await apiJson(`/session/${closedSess.session_id}/csat`, { method: 'POST', token, body: { score: 4 } });
    record('A001', first.status === 201 && second.status === 409, `first=${first.status} second=${second.status}`);
  } else {
    record('A001', false, '无 closed 会话');
  }

  // A002 留言建单：waiting_human 无坐席 → 建工单
  if (waitingSess) {
    const lm = await apiJson(`/session/${waitingSess.session_id}/leave-message`, { method: 'POST', token, body: { content: 'E2E 留言：请尽快联系我处理订单问题' } });
    record('A002', (lm.status === 201 || lm.status === 200) && !!lm.data?.ticket_no, `status=${lm.status} ticket=${lm.data?.ticket_no}`);
  } else {
    record('A002', false, '无 waiting_human 会话');
  }

  // A004 WebSocket：带 token 连接成功；无 token 被拒（1008）
  {
    // : 必须使用 /auth/ws-token 签发的短效工作台令牌
    const wsTokenResp = await apiJson('/auth/ws-token', { method: 'POST', token });
    const wsToken = wsTokenResp.data?.token;
    const wsOk = await new Promise((resolve) => {
      const ws = new WebSocket(`ws://localhost:8000/api/v1/ws/agent?token=${encodeURIComponent(wsToken)}`);
      const t = setTimeout(() => { ws.close(); resolve(false); }, 8000);
      ws.onopen = () => { clearTimeout(t); ws.close(); resolve(true); };
      ws.onerror = () => { clearTimeout(t); resolve(false); };
      ws.onclose = (e) => { if (e.code === 1008 || e.code === 1006) { clearTimeout(t); resolve(`closed-${e.code}`); } };
    });
    const wsReject = await new Promise((resolve) => {
      const ws = new WebSocket('ws://localhost:8000/api/v1/ws/agent');
      const t = setTimeout(() => { ws.close(); resolve(false); }, 8000);
      ws.onopen = () => { clearTimeout(t); ws.close(); resolve('opened'); };
      ws.onclose = (e) => { if (e.code === 1008 || e.code === 1006) { clearTimeout(t); resolve(`closed-${e.code}`); } };
      ws.onerror = () => { /* close 会触发 */ };
    });
    record('A004', wsOk === true, `token=${wsOk}`);
    record('A004b', wsReject.startsWith('closed-'), `no-token=${wsReject}`);
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== API SUMMARY: ${results.length - failed.length}/${results.length} passed ===`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => `${f.id} :: ${f.detail}`).join('\n'));
    process.exitCode = 1;
  }
})();
