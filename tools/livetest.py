"""HY2 节点真实存活压测

每个节点：起一个独立 hysteria2 客户端进程（只监听 127.0.0.1:<独立端口>）
-> 通过该 SOCKS5 真实请求 HTTP -> 记录出口 IP / RTT / 下载速率
注意：全程不设置系统代理、不读写 v2ray 配置、不占用 10808/10809 端口。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import socks  # PySocks

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
HY = os.path.join(WORK, 'bin', 'hysteria.exe')
OUTDIR = os.path.join(WORK, 'recon', 'livetest')
os.makedirs(OUTDIR, exist_ok=True)

IP_API = 'http://ipinfo.io/json'
DEFAULT_SNI_OK = 'centos.org'


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
                'ip': node['ip'], 'port': node['port'], 'location': node['location'],
                'sni': cfg.get('@HY2_DOMAIN') or DEFAULT_SNI_OK,
                'auth': cfg.get('@HY2_AUTH', ''),
            })
    return inv['account'], nodes


def write_cfg(node, socks_port):
    cfg = {
        'server': '%s:%d' % (node['ip'], node['port']),
        'auth': node['auth'],
        'tls': {'sni': node['sni'], 'insecure': True},
        'fastOpen': True,
        'socks5': {'listen': '127.0.0.1:%d' % socks_port},
        'quic': {'maxIdleTimeout': '20s', 'keepAlivePeriod': '5s',
                 'disablePathMTUDiscovery': True},
        'congestion': {'type': 'bbr', 'bbrProfile': 'standard'},
    }
    path = os.path.join(OUTDIR, 'cfg_%s_%d.yaml' % (node['ip'].replace('.', '_'), node['port']))
    with open(path, 'w', encoding='utf-8') as fh:
        for k, v in cfg.items():
            if isinstance(v, dict):
                fh.write('%s:\n' % k)
                for kk, vv in v.items():
                    fh.write('  %s: %s\n' % (kk, json.dumps(vv) if isinstance(vv, bool) else vv))
            elif isinstance(v, bool):
                fh.write('%s: %s\n' % (k, 'true' if v else 'false'))
            else:
                fh.write('%s: %s\n' % (k, v))
    return path


def wait_port(port, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(('127.0.0.1', port), timeout=1)
            s.close()
            return True
        except Exception:
            time.sleep(0.15)
    return False


def http_via_socks(port, url=IP_API, timeout=12, read_limit=None):
    """通过 SOCKS5 发一个真实 HTTP 请求，返回 (status, body, rtt_ms)"""
    from urllib.parse import urlparse
    u = urlparse(url)
    host, path = u.hostname, u.path or '/'
    t0 = time.time()
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, '127.0.0.1', port)
    s.settimeout(timeout)
    s.connect((host, u.port or 80))
    req = 'GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: curl/8\r\nConnection: close\r\n\r\n' % (path, host)
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


def test_node(node, idx, keep_logs=False):
    socks_port = 29000 + idx
    cfg = write_cfg(node, socks_port)
    log = open(os.path.join(OUTDIR, 'log_%s_%d.txt' % (node['ip'].replace('.', '_'), node['port'])), 'w')
    proc = subprocess.Popen([HY, 'client', '-c', cfg], stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    result = dict(node)
    result.update({'ok': False, 'egress_ip': None, 'egress_org': None,
                   'rtt_ms': None, 'error': None})
    try:
        if not wait_port(socks_port, 8):
            result['error'] = 'socks not up'
            return result
        status, body, rtt = http_via_socks(socks_port)
        if status != 200:
            result['error'] = 'http %s' % status
            return result
        info = json.loads(body.decode('utf-8', 'replace'))
        result.update({'ok': True, 'egress_ip': info.get('ip'),
                       'egress_org': (info.get('org') or '')[:40],
                       'egress_country': info.get('country'), 'rtt_ms': rtt})
    except Exception as exc:
        result['error'] = type(exc).__name__ + ':' + str(exc)[:60]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log.close()
        if not keep_logs:
            try:
                os.remove(cfg)
            except Exception:
                pass
    return result


def main():
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    account, nodes = load_nodes()
    print('nodes:', len(nodes), 'concurrency:', batch)
    results = []

    def job(args):
        idx, node = args
        r = test_node(node, idx)
        flag = 'OK  ' if r['ok'] else 'FAIL'
        print('%s %-16s:%-5d %-4s rtt=%-6s ip=%-16s %s' % (
            flag, r['ip'], r['port'], r['location'], r['rtt_ms'],
            r['egress_ip'] or '-', r['error'] or r['egress_org'] or ''))
        return r

    with ThreadPoolExecutor(max_workers=batch) as pool:
        for r in pool.map(job, list(enumerate(nodes))):
            results.append(r)

    alive = [r for r in results if r['ok']]
    payload = {'tested_at': time.strftime('%Y-%m-%d %H:%M:%S'),
               'total': len(results), 'alive': len(alive), 'results': results}
    json.dump(payload, open(os.path.join(OUTDIR, 'livetest.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n==== alive %d / %d ====' % (len(alive), len(results)))
    for r in sorted(alive, key=lambda x: x['rtt_ms'] or 9999):
        print('  %-16s %-5d %-4s %5sms %-16s %s' % (r['ip'], r['port'], r['location'],
                                                    r['rtt_ms'], r['egress_ip'], r['egress_org']))


if __name__ == '__main__':
    main()
