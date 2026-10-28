"""用内置 mihomo(Clash.Meta) 内核把 65 个 hysteria2 节点转成本地独立 SOCKS5 端口并压测

- 每个节点一个 listeners 项：127.0.0.1:(41000+i) -> 指定 hysteria2 代理
- 全程不设置系统代理、不读写 v2ray/v2rayN 配置、不占用 10808/10809/7890
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
RUNDIR = os.path.join(WORK, 'recon', 'pool')
os.makedirs(RUNDIR, exist_ok=True)

_MIHOMO_DEFAULT = 'mihomo.exe' if os.name == 'nt' else 'mihomo'
MIHOMO = os.environ.get('MIHOMO_BIN') or shutil.which(_MIHOMO_DEFAULT) or _MIHOMO_DEFAULT

SOCKS_BASE = 41000
CTRL = '127.0.0.1:19099'
IP_API = 'http://ip-api.com/json/?fields=status,country,countryCode,isp,query'


def load_nodes():
    inv = json.load(open(os.path.join(WORK, 'recon', 'harvest', 'inventory.json'), encoding='utf-8'))
    nodes, seen = [], set()
    for loc, data in inv['locations'].items():
        for node in data['hy2']:
            key = (node['ip'], node['port'])
            if key in seen:
                continue
            seen.add(key)
            cfg = node.get('config') or {}
            nodes.append({
                'idx': len(nodes),
                'name': 'SVPN-%s-%s' % (loc.upper(), node['ip'].replace('.', '_')),
                'ip': node['ip'], 'port': node['port'], 'location': node['location'],
                'sni': cfg.get('@HY2_DOMAIN') or 'centos.org',
                'auth': cfg.get('@HY2_AUTH', ''),
                'socks_port': SOCKS_BASE + len(nodes),
            })
    return nodes


def build_config(nodes, path):
    proxies, listeners = [], []
    for n in nodes:
        proxies.append({
            'name': n['name'], 'type': 'hysteria2',
            'server': n['ip'], 'port': n['port'],
            'password': n['auth'], 'sni': n['sni'], 'skip-cert-verify': True,
            'up': '100 Mbps', 'down': '200 Mbps',
        })
        listeners.append({
            'name': 'li-%d' % n['idx'], 'type': 'socks',
            'listen': '127.0.0.1', 'port': n['socks_port'],
            'proxy': n['name'], 'udp': True,
        })
    cfg = {
        'log-level': 'warning',
        'mode': 'global',
        'external-controller': CTRL,
        'profile': {'store-selected': False, 'store-fake-ip': False},
        'proxies': proxies,
        'listeners': listeners,
        'rules': ['MATCH,DIRECT'],
    }
    with open(path, 'w', encoding='utf-8') as fh:
        yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
    return cfg


def wait_port(port, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(('127.0.0.1', port), timeout=1)
            s.close()
            return True
        except Exception:
            time.sleep(0.2)
    return False


def http_via_socks(port, url=IP_API, timeout=15, read_limit=None, host_header=None):
    from urllib.parse import urlparse
    import socks as pysocks
    u = urlparse(url)
    host, path = u.hostname, (u.path or '/') + (('?' + u.query) if u.query else '')
    t0 = time.time()
    s = pysocks.socksocket()
    s.set_proxy(pysocks.SOCKS5, '127.0.0.1', port)
    s.settimeout(timeout)
    s.connect((host, u.port or 80))
    req = 'GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: curl/8\r\nAccept: */*\r\nConnection: close\r\n\r\n' % (
        path, host_header or host)
    s.sendall(req.encode())
    data = b''
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
        if read_limit and len(data) >= read_limit:
            break
    s.close()
    rtt = int((time.time() - t0) * 1000)
    head, _, body = data.partition(b'\r\n\r\n')
    status = 0
    try:
        status = int(head.split(b'\r\n')[0].split()[1])
    except Exception:
        pass
    return status, body, rtt


def test_one(node, timeout=15):
    res = dict(node)
    res.update({'ok': False, 'egress_ip': None, 'egress_country': None,
                'egress_isp': None, 'rtt_ms': None, 'error': None})
    try:
        status, body, rtt = http_via_socks(node['socks_port'], timeout=timeout)
        if status != 200:
            res['error'] = 'http %s %r' % (status, body[:60])
            return res
        info = json.loads(body.decode('utf-8', 'replace'))
        if info.get('status') != 'success':
            res['error'] = str(info)[:80]
            return res
        res.update({'ok': True, 'egress_ip': info.get('query'),
                    'egress_country': info.get('countryCode'),
                    'egress_isp': (info.get('isp') or '')[:38], 'rtt_ms': rtt})
    except Exception as exc:
        res['error'] = type(exc).__name__ + ':' + str(exc)[:50]
    return res


def main():
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    nodes = load_nodes()
    cfg_path = os.path.join(RUNDIR, 'config.yaml')
    build_config(nodes, cfg_path)
    print('[i] config ->', cfg_path, '| nodes', len(nodes))

    log = open(os.path.join(RUNDIR, 'mihomo.log'), 'w')
    proc = subprocess.Popen([MIHOMO, '-d', RUNDIR, '-f', cfg_path],
                            stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    print('[i] mihomo pid', proc.pid)
    try:
        up = wait_port(nodes[0]['socks_port'], 25)
        print('[i] socks listener up:', up)
        if not up:
            print(open(os.path.join(RUNDIR, 'mihomo.log'), encoding='utf-8', errors='replace').read()[-1500:])
            return

        def job(n):
            r = test_one(n)
            print('%s %-16s:%-5d %-4s %6sms %-16s %-3s %s' % (
                'OK  ' if r['ok'] else 'FAIL', r['ip'], r['port'], r['location'],
                r['rtt_ms'], r['egress_ip'] or '-', r['egress_country'] or '--',
                r['egress_isp'] or r['error'] or ''))
            return r

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=batch) as pool:
            results = list(pool.map(job, nodes))
        elapsed = int(time.time() - t0)
        alive = [r for r in results if r['ok']]
        print('\n==== alive %d / %d  (%ds) ====' % (len(alive), len(results), elapsed))
        json.dump({'tested_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'total': len(results), 'alive': len(alive),
                   'elapsed_s': elapsed, 'results': results},
                  open(os.path.join(RUNDIR, 'livetest.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print('[i] mihomo 仍在运行 pid=%d（供 demo/压测复用），停止: taskkill /PID %d /F'
              % (proc.pid, proc.pid))
    finally:
        log.close()


if __name__ == '__main__':
    main()
