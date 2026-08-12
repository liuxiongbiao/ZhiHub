/* 014 Widget 公共通道前端 SDK（自托管 /widget.js，由 nginx 静态服务）
 * 用法: <script src="/widget.js" data-api-key="..." data-endpoint="/api/v1/widget"
 *        data-theme-color="#1890ff" async></script>
 */
(function () {
  var s = document.currentScript;
  if (!s) return;
  var apiKey = s.getAttribute('data-api-key') || '';
  var endpoint = s.getAttribute('data-endpoint') || '/api/v1/widget';
  var theme = s.getAttribute('data-theme-color') || '#1890ff';

  var root = document.createElement('div');
  root.id = 'zhihub-widget-root';
  root.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483000;font-family:sans-serif;';
  document.body.appendChild(root);

  var box = document.createElement('div');
  box.style.cssText = 'display:none;width:320px;height:420px;border:1px solid #e5e5e5;border-radius:10px;' +
    'box-shadow:0 4px 20px rgba(0,0,0,.15);background:#fff;flex-direction:column;overflow:hidden;';
  root.appendChild(box);

  var header = document.createElement('div');
  header.textContent = '智能客服';
  header.style.cssText = 'padding:10px 14px;color:#fff;background:' + theme + ';font-weight:600;';
  box.appendChild(header);

  var log = document.createElement('div');
  log.style.cssText = 'flex:1;overflow-y:auto;padding:10px;font-size:13px;line-height:1.6;';
  box.appendChild(log);

  var form = document.createElement('form');
  form.style.cssText = 'display:flex;border-top:1px solid #eee;';
  var input = document.createElement('input');
  input.placeholder = '请输入您的问题…';
  input.style.cssText = 'flex:1;border:0;padding:10px;outline:none;font-size:13px;';
  var send = document.createElement('button');
  send.textContent = '发送';
  send.style.cssText = 'border:0;padding:0 14px;color:#fff;background:' + theme + ';cursor:pointer;';
  form.appendChild(input);
  form.appendChild(send);
  box.appendChild(form);

  var btn = document.createElement('button');
  btn.textContent = '在线咨询';
  btn.style.cssText = 'padding:10px 16px;border:0;border-radius:20px;color:#fff;background:' + theme + ';cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.2);';
  root.appendChild(btn);

  var session = null;
  function appendMsg(role, text) {
    var p = document.createElement('div');
    p.style.cssText = 'margin:6px 0;text-align:' + (role === 'user' ? 'right' : 'left');
    var b = document.createElement('div');
    b.style.cssText = 'display:inline-block;max-width:85%;padding:6px 10px;border-radius:8px;' +
      (role === 'user' ? 'background:' + theme + ';color:#fff;' : 'background:#f2f3f5;color:#333;');
    b.textContent = text;
    p.appendChild(b);
    log.appendChild(p);
    log.scrollTop = log.scrollHeight;
  }

  function post(path, body, token) {
    return fetch(endpoint + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': token ? 'Bearer ' + token : '' },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json().then(function (j) { return { status: r.status, data: j }; }); });
  }

  function ensureSession() {
    if (session) return Promise.resolve(session);
    return post('/session', { api_key: apiKey }).then(function (res) {
      if (res.status !== 201) throw new Error(res.data.detail || '初始化失败');
      session = res.data;
      return session;
    });
  }

  btn.addEventListener('click', function () {
    var open = box.style.display === 'flex';
    box.style.display = open ? 'none' : 'flex';
    btn.textContent = open ? '在线咨询' : '关闭';
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    appendMsg('user', text);
    ensureSession().then(function (sess) {
      return post('/' + sess.session_id + '/message', { session_id: sess.session_id, content: text }, sess.token);
    }).then(function (res) {
      appendMsg('assistant', (res.data && (res.data.content || res.data.detail)) || '暂时无法回复，请稍后重试');
    }).catch(function (err) {
      appendMsg('assistant', err.message || '网络异常，请稍后重试');
    });
  });
})();
