/* 规格 014/ Widget 公共通道前端 SDK（自托管 /widget.js，由 nginx/前端静态服务）
 * 用法: <script src="https://chat.example.com/widget.js"
 *          data-api-key="..."
 *          data-endpoint="https://chat.example.com/api/v1/widget"
 *          data-ws-endpoint="wss://chat.example.com/api/v1/ws"   // 缺省从 endpoint 推导
 *          data-theme-color="#1890ff" async></script>
 *
 *  能力:
 *   - 匿名会话 + Widget token，连接终端用户 WebSocket 实时接收人工客服回复
 *   - 事件: waiting_human / human_claimed / human_returned / session_ended / agent_message
 *   - 断线有限次重连（指数退避，上限 5 次）；token 过期清理会话提示重新开始
 *   - 发送期间禁用提交防重复；AI 回复展示 sources；degraded/转人工状态提示
 *   - 全部内容用 textContent 渲染，不使用 innerHTML（防 XSS）
 *
 *  能力:
 *   - 会话响应携带租户 theme_color/logo_url/welcome_text，客户端真实应用品牌
 *     （主题色只接受 #rrggbb，logo 只接受绝对 http/https，非法值忽略）
 *   - sources 引用兼容 doc 字段（后端文档名实际字段）
 *   - window.ZhiHubWidget.destroy(): 关闭 WebSocket、清除全部定时器、移除 DOM，
 *     供宿主页（如 WidgetPreview 重复加载）彻底销毁实例
 */
(function () {
  var s = document.currentScript;
  if (!s) return;
  var apiKey = s.getAttribute('data-api-key') || '';
  var endpoint = (s.getAttribute('data-endpoint') || '/api/v1/widget').replace(/\/+$/, '');
  var attrTheme = normalizeTheme(s.getAttribute('data-theme-color'));
  // ws 地址优先取 data-ws-endpoint；否则按 endpoint scheme 推导（http→ws / https→wss）
  var wsEndpoint = s.getAttribute('data-ws-endpoint');
  if (!wsEndpoint) {
    wsEndpoint = endpoint.replace(/^http/, 'ws').replace(/\/api\/v1\/widget.*$/, '') + '/api/v1/ws';
  }
  wsEndpoint = wsEndpoint.replace(/\/+$/, '');

  var WS_MAX_RETRIES = 5;          // 断线重连上限（禁止无限重试）
  var WS_RETRY_BASE_MS = 1500;     // 重连退避基数（指数退避）

  // ── 品牌值安全校验（与后端写入/读取规则一致，客户端兜底）──
  function normalizeTheme(value) {
    return /^#[0-9a-fA-F]{6}$/.test(value || '') ? value : null;
  }
  /** : 严格 Logo URL 校验（与后端同一口径）——用 URL 解析而非前缀正则：
   *  仅 http/https、必须有 hostname、无凭据/query/fragment/控制字符、端口合法。 */
  function normalizeLogoUrl(value) {
    if (!value || /[\x00-\x1f\x7f]/.test(value)) return null;
    var parsed;
    try { parsed = new URL(value); } catch (e) { return null; }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    if (!parsed.hostname) return null;
    if (parsed.username || parsed.password) return null;
    if (parsed.search || parsed.hash) return null;
    return value;
  }
  /** 引用文档名: 后端字段实际为 doc（兼容 title/doc_title/source） */
  function sourceLabel(x) {
    return x && (x.doc || x.title || x.doc_title || x.source) || '来源';
  }

  // ── 定时器登记表: destroy() 时统一清理，防残留重连 ──
  var pendingTimers = [];
  function setTimer(fn, ms) {
    var id = setTimeout(fn, ms);
    pendingTimers.push(id);
    return id;
  }
  function clearTimers() {
    pendingTimers.forEach(clearTimeout);
    pendingTimers = [];
  }

  // ── DOM 组装（全部 createElement + textContent，无 innerHTML）──
  var root = document.createElement('div');
  root.id = 'zhihub-widget-root';
  root.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483000;font-family:sans-serif;';
  document.body.appendChild(root);

  var box = document.createElement('div');
  box.className = 'zhihub-widget-box';
  box.style.cssText = 'display:none;width:320px;height:440px;border:1px solid #e5e5e5;border-radius:10px;' +
    'box-shadow:0 4px 20px rgba(0,0,0,.15);background:#fff;flex-direction:column;overflow:hidden;';
  root.appendChild(box);

  var header = document.createElement('div');
  header.className = 'zhihub-widget-header';
  header.style.cssText = 'display:flex;align-items:center;padding:10px 14px;color:#fff;background:' +
    (attrTheme || '#1890ff') + ';font-weight:600;';
  var headerText = document.createElement('span');
  headerText.textContent = '智能客服';
  header.appendChild(headerText);
  box.appendChild(header);

  var status = document.createElement('div');
  status.className = 'zhihub-widget-status';
  status.style.cssText = 'display:none;padding:6px 14px;font-size:12px;background:#fdf6ec;color:#b88230;border-bottom:1px solid #faecd8;';
  box.appendChild(status);

  var log = document.createElement('div');
  log.className = 'zhihub-widget-log';
  log.style.cssText = 'flex:1;overflow-y:auto;padding:10px;font-size:13px;line-height:1.6;';
  box.appendChild(log);

  var form = document.createElement('form');
  form.style.cssText = 'display:flex;border-top:1px solid #eee;';
  var input = document.createElement('input');
  input.placeholder = '请输入您的问题…';
  input.style.cssText = 'flex:1;border:0;padding:10px;outline:none;font-size:13px;';
  var send = document.createElement('button');
  send.textContent = '发送';
  send.style.cssText = 'border:0;padding:0 14px;color:#fff;background:' + (attrTheme || '#1890ff') + ';cursor:pointer;';
  form.appendChild(input);
  form.appendChild(send);
  box.appendChild(form);

  var btn = document.createElement('button');
  btn.textContent = '在线咨询';
  btn.className = 'zhihub-widget-launcher';
  btn.style.cssText = 'padding:10px 16px;border:0;border-radius:20px;color:#fff;background:' +
    (attrTheme || '#1890ff') + ';cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.2);';
  root.appendChild(btn);

  // ── 会话状态 ──
  var session = null;          // {session_id, token, expires_in, welcome_text, theme_color, logo_url}
  var theme = attrTheme || '#1890ff';  // 当前生效主题（会话响应可覆盖）
  var ws = null;
  var wsRetries = 0;
  var sending = false;         // 发送中锁：防重复提交
  var seenMsgIds = {};         // WS 消息按 message_id 去重（重连补发/重复事件不重复展示）

  /** 应用主题色: 头部/发送/启动按钮/用户气泡同步换色 */
  function applyTheme(color) {
    var c = normalizeTheme(color);
    if (!c) return;
    theme = c;
    header.style.background = c;
    send.style.background = c;
    btn.style.background = c;
  }

  /** 应用 Logo: img 元素 + 绝对 http/https 校验（无 innerHTML） */
  function applyLogo(url) {
    var u = normalizeLogoUrl(url);
    var old = header.querySelector('img.zhihub-widget-logo');
    if (old) old.remove();
    if (!u) return;
    var img = document.createElement('img');
    img.className = 'zhihub-widget-logo';
    img.alt = '';
    img.style.cssText = 'height:18px;margin-right:8px;vertical-align:middle;border-radius:4px;';
    img.src = u;
    header.insertBefore(img, header.firstChild);
  }

  function appendMsg(role, text) {
    var p = document.createElement('div');
    p.style.cssText = 'margin:6px 0;text-align:' + (role === 'user' ? 'right' : 'left');
    var b = document.createElement('div');
    var bg = role === 'user' ? theme : (role === 'agent' ? '#e8f4ff' : '#f2f3f5');
    b.style.cssText = 'display:inline-block;max-width:85%;padding:6px 10px;border-radius:8px;' +
      'background:' + bg + ';color:' + (role === 'user' ? '#fff' : '#333') + ';text-align:left;';
    b.textContent = text;
    p.appendChild(b);
    log.appendChild(p);
    log.scrollTop = log.scrollHeight;
    return b;
  }

  /** AI 回复附加 sources 引用列表（textContent 渲染，兼容 doc 字段） */
  function appendSources(bubble, sources) {
    if (!sources || !sources.length) return;
    var div = document.createElement('div');
    div.style.cssText = 'margin-top:4px;font-size:11px;opacity:.75;';
    div.textContent = '引用: ' + sources.map(sourceLabel).join('; ');
    bubble.appendChild(div);
  }

  function showStatus(text) {
    status.textContent = text;
    status.style.display = text ? 'block' : 'none';
  }

  function post(path, body, token) {
    // 无 token 时完全不带 Authorization 头（创建会话是公开端点，空 Bearer 头会被鉴权中间件误判）
    var headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return fetch(endpoint + path, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, data: j }; })
        .catch(function () { return { status: r.status, data: {} }; });
    });
  }

  /** 发送 SSE 请求并逐块回调文本；返回最终 message_id、sources 与 metadata。 */
  function postStream(path, body, token, onChunk) {
    var headers = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = 'Bearer ' + token;
    return fetch(endpoint + path, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(body),
    }).then(function (response) {
      if (!response.ok) {
        return response.json().then(function (data) {
          return { status: response.status, data: data };
        }).catch(function () {
          return { status: response.status, data: {} };
        });
      }

      var reader = response.body && response.body.getReader ? response.body.getReader() : null;
      if (!reader) throw new Error('当前浏览器不支持流式回复');
      var decoder = new TextDecoder('utf-8');
      var buffer = '';
      var finalData = {};

      function consumeEvent(block) {
        var dataLines = block.split(/\r?\n/).filter(function (line) {
          return line.indexOf('data:') === 0;
        }).map(function (line) {
          return line.slice(5).trimStart();
        });
        if (!dataLines.length) return;
        var raw = dataLines.join('\n');
        if (raw === '[DONE]') return;
        var event;
        try { event = JSON.parse(raw); } catch (e) { return; }
        if (event.error) throw new Error(event.error);
        if (event.chunk) onChunk(event.chunk);
        if (event.message_id) finalData.message_id = event.message_id;
        if (event.sources) finalData.sources = event.sources;
        if (event.metadata) finalData.metadata = event.metadata;
      }

      function consumeBuffer(flush) {
        var blocks = buffer.split(/\r?\n\r?\n/);
        buffer = flush ? '' : blocks.pop();
        blocks.forEach(consumeEvent);
        if (flush && buffer) consumeEvent(buffer);
      }

      function pump() {
        return reader.read().then(function (part) {
          buffer += decoder.decode(part.value || new Uint8Array(), { stream: !part.done });
          consumeBuffer(part.done);
          if (part.done) return { status: response.status, data: finalData };
          return pump();
        });
      }
      return pump();
    });
  }

  /** token 过期/失效: 清理旧会话与连接，提示重新开始 */
  function resetSession(reason) {
    closeWs();
    session = null;
    seenMsgIds = {};
    showStatus(reason || '会话已过期，请重新发送消息开始新会话');
  }

  function closeWs() {
    if (ws) {
      try { ws.onclose = null; ws.close(); } catch (e) { /* 已关闭 */ }
      ws = null;
    }
  }

  /** 连接终端用户 WS 并订阅事件；断线按指数退避有限次重连。
   *  返回 Promise: 打开（服务端已注册会话连接）后 resolve，保证后续消息触发的
   *  waiting_human 等事件广播不会早于连接注册而丢失（时序竞争防护）。 */
  function connectWs(sess) {
    closeWs();
    return new Promise(function (resolve) {
      var opened = false;
      var done = function () { if (!opened) { opened = true; resolve(); } };
      try {
        ws = new WebSocket(wsEndpoint + '/session/' + sess.session_id + '?token=' + encodeURIComponent(sess.token));
      } catch (e) {
        scheduleReconnect(sess);
        done();
        return;
      }
      ws.onopen = function () { wsRetries = 0; done(); };
      ws.onmessage = function (ev) {
        var msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        handleEvent(msg);
      };
      ws.onclose = function (ev) {
        // 1008 = 策略拒绝（token 无效/过期），不重连，交给消息链路触发会话重建
        if (ev && ev.code === 1008) {
          if (session && session.session_id === sess.session_id) resetSession();
          done();
          return;
        }
        done();
        scheduleReconnect(sess);
      };
      ws.onerror = function () { /* onclose 兜底处理重连 */ };
      // 3s 未打开视为连接失败，继续发消息（HTTP 通道不依赖 WS）
      setTimer(function () { done(); }, 3000);
    });
  }

  function scheduleReconnect(sess) {
    if (!session || session.session_id !== sess.session_id) return; // 会话已换/已过期，不重连
    if (wsRetries >= WS_MAX_RETRIES) {
      showStatus('连接已断开，发送下一条消息将自动重试');
      return;
    }
    wsRetries += 1;
    var delay = WS_RETRY_BASE_MS * Math.pow(2, wsRetries - 1);
    setTimer(function () {
      if (session && session.session_id === sess.session_id && (!ws || ws.readyState > 1)) {
        connectWs(sess);
      }
    }, delay);
  }

  /** 统一事件 envelope: {type, payload}（兼容平铺旧字段） */
  function handleEvent(msg) {
    var type = msg && (msg.type || msg.event);
    var p = (msg && msg.payload) || msg || {};
    if (type === 'agent_message') {
      var mid = p.message_id || msg.message_id;
      if (mid && seenMsgIds[mid]) return; // 去重
      if (mid) seenMsgIds[mid] = true;
      var bubble = appendMsg('agent', p.content || msg.content || '');
      appendSources(bubble, p.sources || msg.sources);
      return;
    }
    if (type === 'waiting_human') {
      showStatus('正在为您转接人工客服，请稍候…');
      return;
    }
    if (type === 'human_claimed') {
      showStatus('人工客服已接入');
      return;
    }
    if (type === 'human_returned') {
      showStatus('已转回智能助手继续为您服务');
      return;
    }
    if (type === 'session_ended') {
      // 清旧会话并把提示交给 resetSession 展示（避免先显示再被空串覆盖）
      resetSession('本次会话已结束，发送消息将开始新会话');
      return;
    }
  }

  function ensureSession() {
    if (session) return Promise.resolve(session);
    // Origin 显式带上：外部站点嵌入时后端域名白名单以 Origin 判定
    return post('/session', { api_key: apiKey, origin: window.location.origin }).then(function (res) {
      if (res.status !== 201) throw new Error(res.data.detail || '初始化失败');
      session = res.data;
      // : 应用租户品牌（主题/Logo/欢迎语），会话响应值覆盖 script 属性默认值
      applyTheme(session.theme_color);
      applyLogo(session.logo_url);
      if (session.welcome_text) appendMsg('assistant', session.welcome_text);
      // 等 WS 打开（服务端注册会话连接）再继续，避免事件广播先于连接注册丢失
      return connectWs(session).then(function () { return session; });
    });
  }

  btn.addEventListener('click', function () {
    var open = box.style.display === 'flex';
    box.style.display = open ? 'none' : 'flex';
    btn.textContent = open ? '在线咨询' : '关闭';
    if (!open) input.focus();
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = input.value.trim();
    // 发送中锁: 请求未返回前禁止重复提交
    if (!text || sending) return;
    sending = true;
    send.disabled = true;
    send.textContent = '发送中';
    input.value = '';
    appendMsg('user', text);
    showStatus('智能助手正在输入…');
    var streamBubble = null;
    ensureSession().then(function (sess) {
      return postStream(
        '/' + sess.session_id + '/message/stream',
        { session_id: sess.session_id, content: text },
        sess.token,
        function (chunk) {
          if (!streamBubble) streamBubble = appendMsg('assistant', '');
          streamBubble.appendChild(document.createTextNode(chunk));
          log.scrollTop = log.scrollHeight;
        }
      );
    }).then(function (res) {
      if (res.status === 401 || res.status === 403) {
        // token 过期/会话令牌不匹配: 清理旧会话，提示重新开始（下一条消息自动重建）
        resetSession(res.status === 401 ? '会话已过期，请重新发送消息开始新会话' : '');
        return;
      }
      if (res.status === 429) {
        showStatus(res.data.detail || '您提问过于频繁，请稍后再试');
        return;
      }
      if (res.status !== 200) {
        appendMsg('assistant', (res.data && res.data.detail) || '暂时无法回复，请稍后重试');
        return;
      }
      var d = res.data || {};
      if (!streamBubble) streamBubble = appendMsg('assistant', '暂时无法回复，请稍后重试');
      appendSources(streamBubble, d.sources);
      // 仅降级模式设置状态；普通回复不清状态，避免覆盖 WS 先到的转人工提示
      if (d.metadata && d.metadata.degraded) {
        showStatus('当前为降级模式，回答质量可能受限');
      } else if (status.textContent === '智能助手正在输入…') {
        showStatus('');
      }
    }).catch(function (err) {
      appendMsg('assistant', (err && err.message) || '网络异常，请稍后重试');
    }).finally(function () {
      sending = false;
      send.disabled = false;
      send.textContent = '发送';
    });
  });

  /** : 彻底销毁实例 —— 关闭 WS、清除全部定时器（含重连）、移除 DOM。 */
  function destroy() {
    clearTimers();
    closeWs();
    wsRetries = 0;
    session = null;
    seenMsgIds = {};
    sending = false;
    if (root && root.parentNode) root.parentNode.removeChild(root);
  }

  // 命名空间明确的公共 API（预览页重复加载前调用销毁）
  window.ZhiHubWidget = { destroy: destroy };

  // 页面关闭时主动断开连接（服务端靠 WebSocketDisconnect 清理连接表）
  window.addEventListener('beforeunload', function () { closeWs(); });
})();
