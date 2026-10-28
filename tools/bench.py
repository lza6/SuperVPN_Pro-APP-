"""节点压测：并发能力 / 带宽 / 凭证时效

三项独立测量：
  1. keepalive — 同一批凭证在 T+0/T+60s/T+180s 是否仍可用（判断凭证有效期是否=12h）
  2. throughput — 单节点连续下载 8MB 的实测速率
  3. concurrency — 单节点同时开 8 条隧道并发的成功率
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time

import yaml

from mihomo_pool import MIHOMO, RUNDIR, wait_port

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
OUT = os.path.join(WORK, 'recon', 'bench')
os.makedirs(OUT, exist_ok=True)
SOCKS_BASE = 45000

import socks as pysocks

alive = json.load(open(os.path.join(WORK, 'recon', 'final', 'alive_nodes.json'), encoding='utf-8'))['nodes']


def start_mihomo(nodes, tag, base):
    cfg = {'log-level': 'warning', 'mode': 'global',
           'external-controller': '127.0.0.1:%d' % (19500 + len(tag)),
           'profile': {'store-selected': False, 'store-fake-ip': False},
           'proxies': [], 'listeners': [], 'rules': ['MATCH,DIRECT']}
    for i, n in enumerate(nodes):
        n['socks_port'] = base + i
        cfg['proxies'].append({'name': 'P%d' % i, 'type': 'hysteria2',
                               'server': n['ip'], 'port': n['port'],
                               'password': n['auth'], 'sni': n['sni'],
                               'skip-cert-verify': True,
                               'up': '100 Mbps', 'down': '200 Mbps'})
        cfg['listeners'].append({'name': 'li%d' % i, 'type': 'socks',
                                 'listen': '127.0.0.1', 'port': n['socks_port'],
                                 'proxy': 'P%d' % i, 'udp': True})
    path = os.path.join(RUNDIR, 'bench_%s.yaml' % tag)
    yaml.safe_dump(cfg, open(path, 'w', encoding='utf-8'), allow_unicode=True, sort_keys=False)
    log = open(os.path.join(RUNDIR, 'bench_%s.log' % tag), 'w')
    proc = subprocess.Popen([MIHOMO, '-d', RUNDIR, '-f', path], stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    wait_port(base, 25)
    return proc, log


def stop(proc, log):
    proc.terminate()
    try:
        proc.wait(timeout=6)
    except Exception:
        proc.kill()
    log.close()


def fetch(port, host, path, limit_bytes=None, timeout=30, tls=False, port_remote=80):
    """通过 socks 端口下载，返回 (bytes, elapsed_s, ttfb_s)"""
    t0 = time.time()
    s = pysocks.socksocket()
    s.set_proxy(pysocks.SOCKS5, '127.0.0.1', port)
    s.settimeout(timeout)
    s.connect((host, port_remote))
    if tls:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        s = ctx.wrap_socket(s, server_hostname=host)
    s.sendall(('GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: curl/8\r\nConnection: close\r\n\r\n'
               % (path, host)).encode())
    total, ttfb = 0, None
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        if ttfb is None:
            ttfb = time.time() - t0
        total += len(chunk)
        if limit_bytes and total >= limit_bytes:
            break
    s.close()
    return total, time.time() - t0, ttfb or 0


def ip_of(port, timeout=15):
    try:
        n, _, _ = fetch(port, 'ip-api.com', '/json/?fields=query,countryCode,isp', timeout=timeout)
    except Exception:
        return None
    s = pysocks.socksocket()
    s.set_proxy(pysocks.SOCKS5, '127.0.0.1', port)
    s.settimeout(timeout)
    try:
        s.connect(('ip-api.com', 80))
        s.sendall(b'GET /json/?fields=query,countryCode,isp HTTP/1.1\r\nHost: ip-api.com\r\nConnection: close\r\n\r\n')
        data = b''
        while True:
            c = s.recv(65536)
            if not c:
                break
            data += c
        return json.loads(data.partition(b'\r\n\r\n')[2].decode())
    except Exception:
        return None
    finally:
        s.close()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'throughput'
    sample = alive[:15]

    if mode == 'throughput':
        # speed.hetzner.de 提供 100MB 测试文件；用 range 限 8MB
        HOST, PATH, LIMIT = 'speed.cloudflare.com', '/__down?bytes=10000000', 10 * 1024 * 1024
        proc, log = start_mihomo(sample, 'tp', SOCKS_BASE)
        out = []
        try:
            for n in sample:
                try:
                    total, el, ttfb = fetch(n['socks_port'], HOST, PATH, LIMIT,
                                            timeout=45, tls=True, port_remote=443)
                    mbps = total * 8 / el / 1e6 if el else 0
                    rec = {'ip': n['ip'], 'port': n['port'], 'rtt_ms': n['rtt_ms'],
                           'bytes': total, 'seconds': round(el, 2), 'ttfb_s': round(ttfb, 2),
                           'mbps': round(mbps, 2), 'egress_country': n['egress_country']}
                    out.append(rec)
                    print('%-16s %-4s %8.2f Mbps  %6.2fs ttfb=%.2fs  %d B' % (
                        n['ip'], n['egress_country'], mbps, el, ttfb, total))
                except Exception as exc:
                    print('%-16s FAIL %s' % (n['ip'], type(exc).__name__))
                    out.append({'ip': n['ip'], 'error': type(exc).__name__})
        finally:
            stop(proc, log)
        json.dump(out, open(os.path.join(OUT, 'throughput.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)

    elif mode == 'concurrency':
        proc, log = start_mihomo(sample[:6], 'cc', SOCKS_BASE)
        out = []
        try:
            for n in sample[:6]:
                results = []

                def worker():
                    try:
                        total, el, _ = fetch(n['socks_port'], 'ip-api.com',
                                             '/json/?fields=query', timeout=20)
                        results.append(('ok', total))
                    except Exception as exc:
                        results.append(('err', type(exc).__name__))

                threads = [threading.Thread(target=worker) for _ in range(8)]
                t0 = time.time()
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                el = time.time() - t0
                ok = sum(1 for r in results if r[0] == 'ok')
                out.append({'ip': n['ip'], 'ok': ok, 'total': 8, 'seconds': round(el, 2)})
                print('%-16s %d/8 并发成功  %.2fs' % (n['ip'], ok, el))
        finally:
            stop(proc, log)
        json.dump(out, open(os.path.join(OUT, 'concurrency.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)

    elif mode == 'credential':
        # 凭证时效：同一批凭证 T+0 / T+120s / T+300s 三次可用性
        probe = alive[:8]
        proc, log = start_mihomo(probe, 'cd', SOCKS_BASE)
        out = []
        try:
            for wait_s in [0, 120, 300]:
                if wait_s:
                    print('... waiting %ds' % wait_s)
                    time.sleep(wait_s)
                row = {'t_plus_s': wait_s, 'ok': 0, 'detail': []}
                for n in probe:
                    info = ip_of(n['socks_port'])
                    good = bool(info and info.get('query'))
                    row['ok'] += 1 if good else 0
                    row['detail'].append({'ip': n['ip'], 'ok': good,
                                          'egress': (info or {}).get('query')})
                out.append(row)
                print('T+%ds: %d/%d 可用' % (wait_s, row['ok'], len(probe)))
        finally:
            stop(proc, log)
        json.dump(out, open(os.path.join(OUT, 'credential.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
