# -*- coding: utf-8 -*-
"""单页前端（M4）。无构建链：一个 HTML 字符串，靠 SSE 驱动进度。

占位符用 __TOKEN__ 形式，避免和 JS/CSS 的花括号冲突。
"""

INDEX_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>会议资料整理</title>
<style>
  :root{--bg:#f6f7f9;--card:#fff;--line:#e3e6ea;--ink:#1f2328;--sub:#6b7280;
        --brand:#1f3864;--ok:#0f7b3f;--warn:#b45309;--err:#b91c1c}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.65 system-ui,-apple-system,"Microsoft YaHei",sans-serif}
  .wrap{max-width:860px;margin:0 auto;padding:32px 20px 64px}
  h1{font-size:22px;margin:0 0 4px;color:var(--brand)}
  .sub{color:var(--sub);font-size:13px;margin-bottom:22px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
        padding:20px;margin-bottom:16px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  input[type=password],input[type=text]{padding:9px 12px;border:1px solid var(--line);
        border-radius:8px;font-size:15px;width:260px}
  button{padding:9px 18px;border:0;border-radius:8px;background:var(--brand);color:#fff;
         font-size:15px;cursor:pointer}
  button:disabled{background:#9aa5b1;cursor:not-allowed}
  .hint{color:var(--sub);font-size:13px}
  .hidden{display:none!important}
  #stages{display:flex;gap:6px;flex-wrap:wrap;margin:18px 0 6px}
  .st{flex:1 1 66px;min-width:66px;text-align:center;padding:7px 4px;border-radius:7px;
      background:#eef1f5;color:var(--sub);font-size:12px;border:1px solid transparent}
  .st.run{background:#e8effb;color:var(--brand);border-color:#b9cdf0}
  .st.ok{background:#e7f6ec;color:var(--ok)}
  .st.bad{background:#fdeaea;color:var(--err)}
  #log{margin-top:14px;max-height:340px;overflow:auto;border-top:1px dashed var(--line);
       padding-top:10px;font:13px/1.7 ui-monospace,Consolas,monospace}
  #log div{white-space:pre-wrap;word-break:break-all}
  #log .t{color:#9aa5b1}
  #log .s{color:var(--brand)}
  #result{margin-top:16px}
  #result a{display:inline-block;margin:6px 10px 0 0;padding:8px 14px;border:1px solid var(--brand);
            border-radius:8px;color:var(--brand);text-decoration:none;font-size:14px}
  .ok{color:var(--ok)} .err{color:var(--err)} .warn{color:var(--warn)}
  #meter{margin-top:10px;color:var(--sub);font-size:13px}
</style></head>
<body><div class="wrap">
  <h1>会议资料整理</h1>
  <div class="sub">上传录音听记 / 图片 / Word → 自动出会议纪要（md + docx）。成品保留 __TTL__ 分钟。</div>

  <div id="login" class="card hidden">
    <div class="row"><input id="pw" type="password" placeholder="口令" autofocus>
      <button id="loginBtn">进入</button></div>
    <div class="hint" id="loginErr"></div>
  </div>

  <div id="main" class="card hidden">
    <div class="row">
      <input id="file" type="file" accept=".docx,.txt,.md,.png,.jpg,.jpeg">
      <input id="title" type="text" placeholder="主标题（可空，自动推断）">
      <button id="go">开始整理</button>
    </div>
    <div class="hint">支持：.docx / .txt / .md / .png / .jpg，单文件 ≤ __MAXMB__ MB</div>
    <div id="stages"></div>
    <div id="meter"></div>
    <div id="log"></div>
    <div id="result"></div>
  </div>
</div>
<script>
const STAGES = __STAGES__;
const stageBox = document.getElementById('stages');
const logBox = document.getElementById('log');
const resultBox = document.getElementById('result');
const meter = document.getElementById('meter');
let timer = null;

STAGES.forEach(([code, name]) => {
  const d = document.createElement('div');
  d.className = 'st'; d.id = 'st-' + code; d.textContent = name;
  stageBox.appendChild(d);
});

function show(id, on) { document.getElementById(id).classList.toggle('hidden', !on); }
function logLine(text, cls) {
  const d = document.createElement('div');
  const t = new Date().toLocaleTimeString('zh-CN', {hour12:false});
  d.innerHTML = '<span class="t">' + t + '</span> ' +
                (cls ? '<span class="' + cls + '">' + text + '</span>' : text);
  logBox.appendChild(d); logBox.scrollTop = logBox.scrollHeight;
}

async function login() {
  const pw = document.getElementById('pw').value;
  const body = new URLSearchParams({password: pw});
  const r = await fetch('/auth/login', {method:'POST', body, redirect:'manual'});
  if (r.status === 401 || r.status === 500) {
    const j = await r.json().catch(() => ({message:'登录失败'}));
    document.getElementById('loginErr').textContent = j.message || '口令不对';
    return;
  }
  boot();
}
async function boot() {
  const r = await fetch('/api/me');
  const j = await r.json();
  show('login', !j.authed); show('main', j.authed);
  if (j.authed) logLine('已登录，请选择文件', 's');
}
document.getElementById('loginBtn').onclick = login;
document.getElementById('pw').addEventListener('keydown', e => { if (e.key === 'Enter') login(); });

document.getElementById('go').onclick = async () => {
  const f = document.getElementById('file').files[0];
  if (!f) { alert('请选择文件'); return; }
  document.getElementById('go').disabled = true;
  resultBox.innerHTML = ''; logBox.innerHTML = ''; meter.textContent = '';
  STAGES.forEach(([c]) => { const e = document.getElementById('st-' + c); e.className = 'st'; });
  const fd = new FormData();
  fd.append('file', f);
  fd.append('title', document.getElementById('title').value || '');
  logLine('上传 ' + f.name + ' (' + (f.size/1048576).toFixed(1) + ' MB)');
  const r = await fetch('/api/tasks', {method:'POST', body: fd});
  if (!r.ok) {
    const j = await r.json().catch(() => ({message:'提交失败'}));
    logLine('提交失败：' + (j.message || r.status), 'err');
    document.getElementById('go').disabled = false; return;
  }
  const {task_id} = await r.json();
  watch(task_id);
};

function watch(id) {
  const es = new EventSource('/api/tasks/' + id + '/events');
  es.addEventListener('stage', ev => {
    const d = JSON.parse(ev.data);
    const el = document.getElementById('st-' + d.stage);
    if (el) { el.className = 'st ' + (d.state === 'done' ? 'ok' : d.state === 'failed' ? 'bad' : 'run'); }
    logLine((d.state === 'running' ? '▶ ' : '✓ ') + d.name +
            (d.elapsed ? '（' + d.elapsed + 's）' : ''), 's');
  });
  es.addEventListener('step', ev => {
    const d = JSON.parse(ev.data);
    logLine('   ' + (d.label || '') + '　' + (d.detail || ''));
  });
  es.addEventListener('metric', ev => {
    const d = JSON.parse(ev.data);
    meter.textContent = '结构：' + d.chapters + ' 章 / ' + d.sections + ' 节 / ' + d.tables + ' 表';
  });
  es.addEventListener('done', ev => {
    const d = JSON.parse(ev.data);
    es.close(); document.getElementById('go').disabled = false;
    logLine('完成，用时 ' + d.elapsed + 's', 'ok');
    if (d.guard) logLine('守恒校验：' + (d.guard.ok ? '通过' : '未通过'), d.guard.ok ? 'ok' : 'warn');
    resultBox.innerHTML = '<div class="ok">整理完成，点击下载：</div>';
    d.files.forEach(f => {
      const a = document.createElement('a');
      a.href = f.url; a.textContent = f.name; resultBox.appendChild(a);
    });
    startCountdown(d.expires_at);
  });
  es.addEventListener('failed', ev => {
    const d = JSON.parse(ev.data);
    es.close(); document.getElementById('go').disabled = false;
    logLine('失败 [' + d.code + '] ' + d.message, 'err');
  });
  es.onerror = () => { /* 浏览器会自动重连；done/failed 时已 close */ };
}

function startCountdown(ts) {
  if (timer) clearInterval(timer);
  const tick = () => {
    const left = Math.max(0, ts - Math.floor(Date.now()/1000));
    const m = String(Math.floor(left/60)).padStart(2,'0');
    const s = String(left%60).padStart(2,'0');
    let el = document.getElementById('cd');
    if (!el) { el = document.createElement('div'); el.id='cd'; el.className='hint'; resultBox.appendChild(el); }
    el.textContent = left > 0 ? ('成品保留剩余 ' + m + ':' + s) : '成品已过期，请重新生成';
    if (left <= 0 && timer) { clearInterval(timer); timer = null; }
  };
  tick(); timer = setInterval(tick, 1000);
}

boot();
</script></body></html>
"""
