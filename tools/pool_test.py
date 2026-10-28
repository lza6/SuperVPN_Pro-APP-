"""对多轮收割到的节点池做全量真实连通性压测（mihomo 每节点独立 socks 端口）"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

from mihomo_pool import MIHOMO, RUNDIR, test_one, wait_port

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
SOCKS_BASE = 42000


def load_pool():
    data = json.load(open(os.path.join(WORK, 'recon', 'harvest', 'hy2_pool.json'), encoding='utf-8'))
    nodes = []
    for i, n in enumerate(data['nodes']):
        nodes.append({
            'idx': i,
            'name': 'SV%d-%s' % (i, n['ip'].replace('.', '_')),
            'ip': n['ip'], 'port': n['port'], 'location': n['location'],
            'sni': n['sni'], 'auth': n['auth'],
            'socks_port': SOCKS_BASE + i,
        })
    return data, nodes


def build_cfg(nodes, path):
    proxies, listeners = [], []
    for n in nodes:
        proxies.append({'name': n['name'], 'type': 'hysteria2',
                        'server': n['ip'], 'port': n['port'],
                        'password': n['auth'], 'sni': n['sni'],
                        'skip-cert-verify': True,
                        'up': '100 Mbps', 'down': '200 Mbps'})
        listeners.append({'name': 'li%d' % n['idx'], 'type': 'socks',
                          'listen': '127.0.0.1', 'port': n['socks_port'],
                          'proxy': n['name'], 'udp': True})
    cfg = {'log-level': 'warning', 'mode': 'global',
           'external-controller': '127.0.0.1:19100',
           'profile': {'store-selected': False, 'store-fake-ip': False},
           'proxies': proxies, 'listeners': listeners, 'rules': ['MATCH,DIRECT']}
    yaml.safe_dump(cfg, open(path, 'w', encoding='utf-8'), allow_unicode=True, sort_keys=False)


def main():
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    meta, nodes = load_pool()
    print('[i] pool nodes', len(nodes), 'concurrency', batch)
    cfg_path = os.path.join(RUNDIR, 'pool_config.yaml')
    build_cfg(nodes, cfg_path)
    log = open(os.path.join(RUNDIR, 'mihomo_pool.log'), 'w')
    proc = subprocess.Popen([MIHOMO, '-d', RUNDIR, '-f', cfg_path], stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    print('[i] mihomo pid', proc.pid)
    try:
        if not wait_port(nodes[0]['socks_port'], 30):
            print('[fail] mihomo not up'); print(open(os.path.join(RUNDIR, 'mihomo_pool.log'), encoding='utf-8', errors='replace').read()[-2000:]); return
        done = {'n': 0}

        def job(n):
            r = test_one(n, timeout=20)
            done['n'] += 1
            if r['ok']:
                print('%4d/%d OK   %-16s:%-5d %-4s %6sms %-16s %-3s %s' % (
                    done['n'], len(nodes), r['ip'], r['port'], r['location'],
                    r['rtt_ms'], r['egress_ip'], r['egress_country'], r['egress_isp']))
            return r

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=batch) as pool:
            results = list(pool.map(job, nodes))
        elapsed = int(time.time() - t0)
        alive = [r for r in results if r['ok']]
        print('\n==== pool alive %d / %d (%ds) ====' % (len(alive), len(results), elapsed))
        json.dump({'tested_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'total': len(results),
                   'alive': len(alive), 'elapsed_s': elapsed, 'results': results},
                  open(os.path.join(RUNDIR, 'pool_livetest.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print('[i] mihomo 保持运行 pid=%d' % proc.pid)
    finally:
        log.close()


if __name__ == '__main__':
    main()
