"""SuperVPN Pro 2.0.9 — 代理池节点源 + 订阅 demo 服务

端点:
  GET  /                      demo 页面
  GET  /api/health            健康检查
  GET  /api/account           当前 SuperVPN 账号（脱敏）
  POST /api/refresh           重新注册/复用账号 + 收割 + 压测，刷新节点池
  GET  /api/nodes             节点池（含存活/时延/出口IP/带宽）
  GET  /api/nodes/alive       仅存活节点
  GET  /api/acquire?location= 实时获取某地区的新鲜凭证（含 hy2/ikev2/tcp 全量）
  GET  /sub/hy2               纯 hysteria2 URI 订阅（明文）
  GET  /sub/hy2/b64           base64 订阅（给 v2rayN/Shadowrocket 等）
  GET  /sub/clash             Clash.Meta / mihomo 订阅
  GET  /sub/singbox           sing-box outbounds
  GET  /api/socks             本机 mihomo 已映射的 socks5 端口清单（给 new-api 代理池用）

只在本机 127.0.0.1 绑定，不改系统代理，不碰 v2rayN。
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
sys.path.insert(0, WORK)

import supervpn_client as sc  # noqa: E402

PORT = int(os.environ.get('SVPN_PORT', '8930'))
STATE = {
    'account': None,
    'nodes': [],
    'last_refresh': None,
    'refreshing': False,
    'refresh_log': [],
}


def log(msg):
    line = '%s %s' % (time.strftime('%H:%M:%S'), msg)
    STATE['refresh_log'].append(line)
    del STATE['refresh_log'][:-120]
    print(line, flush=True)


def load_account():
    path = os.path.join(WORK, 'recon', 'account.json')
    if not os.path.exists(path):
        return None
    data = json.load(open(path, encoding='utf-8'))
    acc = sc.SuperVpnAccount(device_id=data['device_id'], username=data['username'])
    acc.user = data['user']
    acc.gateway = data.get('gateway')
    return acc


def save_account(acc):
    json.dump({'username': acc.username, 'device_id': acc.device_id,
               'user': acc.user, 'gateway': acc.gateway,
               'app_version_code': acc.app_version_code},
              open(os.path.join(WORK, 'recon', 'account.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)


def load_nodes():
    path = os.path.join(WORK, 'recon', 'final', 'alive_nodes.json')
    if os.path.exists(path):
        STATE['nodes'] = json.load(open(path, encoding='utf-8'))['nodes']
        STATE['last_refresh'] = time.strftime('%Y-%m-%d %H:%M:%S',
                                              time.localtime(os.path.getmtime(path)))


def hy2_uri(n):
    auth = quote(n['auth'], safe='')
    name = quote('SVPN-%s-%s' % (n['egress_country'] or n['location'], n['ip']), safe='')
    return 'hysteria2://%s@%s:%d/?sni=%s&insecure=1#%s' % (
        auth, n['ip'], n['port'], n['sni'], name)


def do_refresh(rounds=2):
    if STATE['refreshing']:
        return
    STATE['refreshing'] = True
    try:
        log('refresh start rounds=%d' % rounds)
        acc = load_account() or sc.SuperVpnAccount()
        try:
            loc = acc.locations()
            if loc.get('status') != 0:
                raise RuntimeError(loc.get('msg'))
        except Exception as exc:
            log('account invalid (%s), re-register' % exc)
            acc = sc.SuperVpnAccount()
            reg = acc.register()
            acc.user = reg['data']['user']
            save_account(acc)
        STATE['account'] = {'username': acc.username,
                            'password': (acc.user or {}).get('password'),
                            'gateway': acc.gateway}
        proc = subprocess.run([sys.executable, os.path.join(WORK, 'harvest_test.py'), str(rounds)],
                              cwd=WORK, capture_output=True, text=True, timeout=1800)
        for line in (proc.stdout or '').splitlines()[-40:]:
            log('  ' + line)
        load_nodes()
        log('refresh done, alive=%d' % len(STATE['nodes']))
    except Exception as exc:
        log('refresh failed: %s' % exc)
    finally:
        STATE['refreshing'] = False


INDEX_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>SuperVPN Pro 2.0.9 代理池</title>
<style>
:root{--bg:#0b1020;--card:#141b30;--fg:#e8edf7;--mut:#8b98b8;--acc:#4d8dff;--ok:#31c48d;--warn:#f0a33a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif}
header{padding:22px 26px;border-bottom:1px solid #22304d;display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
h1{font-size:19px;margin:0;font-weight:650}small{color:var(--mut)}
main{padding:20px 26px;max-width:1280px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-bottom:26px}
.card{background:var(--card);border:1px solid #22304d;border-radius:12px;padding:16px}
.card h3{margin:0 0 10px;font-size:13px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.kv{display:flex;justify-content:space-between;padding:3px 0;font-variant-numeric:tabular-nums}
.kv b{font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #1e2a44}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}
td.num{font-variant-numeric:tabular-nums}
.tag{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;background:#1d2a47;color:#9db4dd}
.ok{color:var(--ok)}.warn{color:var(--warn)}
button{background:var(--acc);border:0;color:#fff;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px}
button:disabled{opacity:.5;cursor:default}
code,pre{font-family:ui-monospace,Consolas,monospace;font-size:12px}
pre{background:#0e1526;border:1px solid #1e2a44;border-radius:10px;padding:12px;overflow:auto}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
#bar{height:6px;background:#1d2a47;border-radius:4px;overflow:hidden;margin-top:10px;display:none}
#bar>i{display:block;height:100%;width:30%;background:var(--acc);animation:mv 1.1s infinite linear}
@keyframes mv{from{transform:translateX(-100%)}to{transform:translateX(340%)}}
</style></head><body>
<header><h1>SuperVPN Pro 2.0.9 · 代理池</h1>
<small id="meta">加载中…</small>
<div style="flex:1"></div>
<button id="btn" onclick="doRefresh()">重新收割 + 压测</button></header>
<div id="bar"><i></i></div>
<main>
<div class="grid" id="cards"></div>
<h3 style="color:#8b98b8;font-size:13px;text-transform:uppercase;letter-spacing:.5px">订阅入口</h3>
<pre id="subs"></pre>
<h3 style="color:#8b98b8;font-size:13px;text-transform:uppercase;letter-spacing:.5px">节点列表</h3>
<table><thead><tr><th>#</th><th>地区</th><th>出口 IP</th><th>运营商</th><th>时延</th><th>带宽</th><th>socks 端口</th></tr></thead>
<tbody id="rows"></tbody></table>
</main>
<script>
const $=s=>document.querySelector(s);
async function j(u){const r=await fetch(u);return r.json()}
function fmtMbps(v){return v? v.toFixed(1)+' Mbps':'—'}
async function load(){
  const h=await j('/api/health');
  $('#meta').textContent=`节点 ${h.alive} 个 · 账号 ${h.account} · 更新 ${h.last_refresh||'—'}`;
  $('#btn').disabled=!!h.refreshing;
  $('#bar').style.display=h.refreshing?'block':'none';
  const d=await j('/api/nodes');
  const tp={},cc={};
  try{ (await j('/api/bench')).throughput.forEach(x=>tp[x.ip+':'+x.port]=x.mbps)}catch(e){}
  try{ (await j('/api/bench')).concurrency.forEach(x=>cc[x.ip]=x.ok)}catch(e){}
  const byC={};d.nodes.forEach(n=>byC[n.egress_country]=(byC[n.egress_country]||0)+1);
  const lat=d.nodes.map(n=>n.rtt_ms).filter(Boolean);
  $('#cards').innerHTML=[
    ['节点池', `<div class="kv"><span>存活</span><b class="ok">${d.nodes.length}</b></div>
      <div class="kv"><span>国家/地区</span><b>${Object.keys(byC).length}</b></div>
      <div class="kv"><span>中位时延</span><b>${lat.length?lat.sort((a,b)=>a-b)[lat.length>>1]:'—'} ms</b></div>`],
    ['地区分布', Object.entries(byC).sort((a,b)=>b[1]-a[1]).slice(0,8)
      .map(([c,n])=>`<div class="kv"><span>${c}</span><b>${n}</b></div>`).join('')],
    ['协议', `<div class="kv"><span>隧道</span><b>Hysteria2 (QUIC)</b></div>
      <div class="kv"><span>传输</span><b>UDP/443</b></div>
      <div class="kv"><span>本地映射</span><b>SOCKS5</b></div>`],
    ['订阅', `<div class="kv"><span>URI</span><a href="/sub/hy2">/sub/hy2</a></div>
      <div class="kv"><span>Base64</span><a href="/sub/hy2/b64">/sub/hy2/b64</a></div>
      <div class="kv"><span>Clash</span><a href="/sub/clash">/sub/clash</a></div>
      <div class="kv"><span>sing-box</span><a href="/sub/singbox">/sub/singbox</a></div>`]
  ].map(([t,b])=>`<div class="card"><h3>${t}</h3>${b}</div>`).join('');
  const base=location.origin;
  $('#subs').textContent=`# 明文 URI 订阅\n${base}/sub/hy2\n\n# Base64（v2rayN / Shadowrocket / NekoBox）\n${base}/sub/hy2/b64\n\n# Clash.Meta / mihomo\n${base}/sub/clash\n\n# sing-box outbounds\n${base}/sub/singbox\n\n# new-api 代理池（本机 socks5 端口清单）\n${base}/api/socks`;
  $('#rows').innerHTML=d.nodes.map((n,i)=>`<tr><td class="num">${i+1}</td>
    <td><span class="tag">${n.egress_country||n.location}</span> ${n.location}</td>
    <td><code>${n.ip}</code></td><td>${n.egress_isp||''}</td>
    <td class="num">${n.rtt_ms} ms</td><td class="num">${fmtMbps(tp[n.ip+':'+n.port])}</td>
    <td class="num">${n.socks_port||'—'}</td></tr>`).join('');
}
async function doRefresh(){
  $('#btn').disabled=true;$('#bar').style.display='block';
  await fetch('/api/refresh',{method:'POST'});
  const t=setInterval(async()=>{const h=await j('/api/health');
    if(!h.refreshing){clearInterval(t);load()}else{$('#meta').textContent='收割压测中…'}},2500);
}
load(); setInterval(load, 30000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = 'SuperVpnPool/1.0'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, indent=1)
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)

        if p == '/':
            return self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
        if p == '/api/health':
            return self._send(200, {
                'ok': True, 'alive': len(STATE['nodes']),
                'account': (STATE['account'] or {}).get('username', '-'),
                'last_refresh': STATE['last_refresh'],
                'refreshing': STATE['refreshing'],
                'log': STATE['refresh_log'][-8:]})
        if p == '/api/account':
            acc = STATE['account'] or {}
            return self._send(200, {'username': acc.get('username'),
                                    'password': acc.get('password'),
                                    'gateway': acc.get('gateway')})
        if p == '/api/nodes':
            return self._send(200, {'last_refresh': STATE['last_refresh'],
                                    'total': len(STATE['nodes']), 'nodes': STATE['nodes']})
        if p == '/api/nodes/alive':
            return self._send(200, {'nodes': [n for n in STATE['nodes'] if n.get('egress_ip')]})
        if p == '/api/bench':
            out = {'throughput': [], 'concurrency': []}
            for key in ('throughput', 'concurrency'):
                fp = os.path.join(WORK, 'recon', 'bench', key + '.json')
                if os.path.exists(fp):
                    out[key] = json.load(open(fp, encoding='utf-8'))
            return self._send(200, out)
        if p == '/api/socks':
            rows = [{'ip': n['ip'], 'port': n['port'], 'country': n.get('egress_country'),
                     'socks5': 'socks5://127.0.0.1:%d' % n['socks_port'],
                     'rtt_ms': n.get('rtt_ms')}
                    for n in STATE['nodes'] if n.get('socks_port')]
            return self._send(200, {'note': '这些端口由 mihomo_pool.py 起的常驻实例提供，需先运行它',
                                    'count': len(rows), 'proxies': rows})
        if p == '/api/acquire':
            loc = (q.get('location') or ['auto'])[0]
            try:
                acc = STATE.get('_acc') or load_account()
                if not acc:
                    return self._send(503, {'error': 'no account'})
                STATE['_acc'] = acc
                res = acc.acquire(loc)
                return self._send(200, res)
            except Exception as exc:
                return self._send(502, {'error': str(exc)})
        if p in ('/sub/hy2', '/sub/hy2/b64', '/sub/clash', '/sub/singbox'):
            nodes = [n for n in STATE['nodes'] if n.get('auth')]
            if p == '/sub/hy2':
                return self._send(200, '\n'.join(hy2_uri(n) for n in nodes) + '\n',
                                  'text/plain; charset=utf-8')
            if p == '/sub/hy2/b64':
                raw = '\n'.join(hy2_uri(n) for n in nodes) + '\n'
                return self._send(200, base64.b64encode(raw.encode()).decode(),
                                  'text/plain; charset=utf-8')
            if p == '/sub/clash':
                return self._send(200, clash_yaml(nodes), 'text/yaml; charset=utf-8')
            return self._send(200, singbox_json(nodes), 'application/json; charset=utf-8')
        return self._send(404, {'error': 'not found'})

    def do_POST(self):
        if urlparse(self.path).path == '/api/refresh':
            if STATE['refreshing']:
                return self._send(202, {'ok': True, 'refreshing': True})
            threading.Thread(target=do_refresh, kwargs={'rounds': 2}, daemon=True).start()
            return self._send(202, {'ok': True, 'refreshing': True})
        return self._send(404, {'error': 'not found'})


def clash_yaml(nodes):
    import yaml
    proxies = []
    for n in nodes:
        proxies.append({'name': 'SVPN-%s-%s' % (n.get('egress_country') or n['location'], n['ip']),
                        'type': 'hysteria2', 'server': n['ip'], 'port': n['port'],
                        'password': n['auth'], 'sni': n['sni'], 'skip-cert-verify': True,
                        'up': '100 Mbps', 'down': '200 Mbps'})
    return yaml.safe_dump({'proxies': proxies,
                           'proxy-groups': [{'name': 'SVPN', 'type': 'select',
                                             'proxies': [p['name'] for p in proxies]}],
                           'rules': ['MATCH,SVPN']},
                          allow_unicode=True, sort_keys=False)


def singbox_json(nodes):
    obs = [{'type': 'hysteria2', 'tag': 'SVPN-%s' % n['ip'],
            'server': n['ip'], 'server_port': n['port'], 'password': n['auth'],
            'tls': {'enabled': True, 'server_name': n['sni'], 'insecure': True}}
           for n in nodes]
    return json.dumps({'outbounds': obs}, ensure_ascii=False, indent=2)


def main():
    acc = load_account()
    if acc:
        STATE['account'] = {'username': acc.username,
                            'password': (acc.user or {}).get('password'),
                            'gateway': acc.gateway}
    load_nodes()
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print('SuperVPN pool demo on http://127.0.0.1:%d  (nodes=%d)' % (PORT, len(STATE['nodes'])),
          flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
