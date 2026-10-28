"""收割 + 即时压测流水线

节点凭证里的 auth 带时间戳（...@<unix>@<md5>），可能有时效。
流程：对每个地区 acquire 一次 -> 立刻用这批新鲜凭证压测 -> 只统计当场可用的节点。
这样得到的是「拿到凭证的当下能不能用」，而不是被过期凭证污染的存活率。
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

import supervpn_client as sc
from mihomo_pool import MIHOMO, RUNDIR, test_one, wait_port

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
OUT = os.path.join(WORK, 'recon', 'final')
SOCKS_BASE = 44000
os.makedirs(OUT, exist_ok=True)

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 2

acct = json.load(open(os.path.join(WORK, 'recon', 'account.json'), encoding='utf-8'))
acc = sc.SuperVpnAccount(device_id=acct['device_id'], username=acct['username'])
acc.user = acct['user']
acc.gateway = acct.get('gateway')

locations = [x['location'] for x in acc.locations()['data']['locations']]
print('[i] locations', locations, 'rounds', ROUNDS)

alive = {}     # (ip,port) -> best record


def run_batch(nodes, tag):
    """起一个临时 mihomo 实例测这批节点"""
    cfg = {'log-level': 'warning', 'mode': 'global',
           'external-controller': '127.0.0.1:%d' % (19200 + (hash(tag) % 300)),
           'profile': {'store-selected': False, 'store-fake-ip': False},
           'proxies': [], 'listeners': [], 'rules': ['MATCH,DIRECT']}
    for i, n in enumerate(nodes):
        n['socks_port'] = SOCKS_BASE + i
        cfg['proxies'].append({'name': n['name'], 'type': 'hysteria2',
                               'server': n['ip'], 'port': n['port'],
                               'password': n['auth'], 'sni': n['sni'],
                               'skip-cert-verify': True,
                               'up': '100 Mbps', 'down': '200 Mbps'})
        cfg['listeners'].append({'name': 'li%d' % i, 'type': 'socks',
                                 'listen': '127.0.0.1', 'port': n['socks_port'],
                                 'proxy': n['name'], 'udp': True})
    path = os.path.join(RUNDIR, 'batch_%s.yaml' % tag)
    yaml.safe_dump(cfg, open(path, 'w', encoding='utf-8'), allow_unicode=True, sort_keys=False)
    log = open(os.path.join(RUNDIR, 'batch_%s.log' % tag), 'w')
    proc = subprocess.Popen([MIHOMO, '-d', RUNDIR, '-f', path], stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        if not wait_port(nodes[0]['socks_port'], 25):
            return []
        with ThreadPoolExecutor(max_workers=min(12, len(nodes))) as pool:
            return list(pool.map(lambda n: test_one(n, timeout=18), nodes))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
        log.close()


for rnd in range(ROUNDS):
    for loc in locations:
        try:
            res = acc.acquire(loc)
        except Exception as exc:
            print('  [warn] acquire %s: %s' % (loc, exc))
            continue
        if res.get('status') != 0:
            print('  [warn] acquire %s status=%s %s' % (loc, res.get('status'), res.get('msg')))
            continue
        data = res['data']
        hy2 = (data.get('hy2Gateways') or []) + (data.get('hy2GatewaysBack') or [])
        batch = []
        for node in hy2:
            cfg = node.get('config') or {}
            batch.append({'name': 'N%d' % len(batch), 'ip': node['ip'], 'port': node['port'],
                          'location': node.get('location') or loc,
                          'sni': cfg.get('@HY2_DOMAIN') or 'centos.org',
                          'auth': cfg.get('@HY2_AUTH', '')})
        if not batch:
            continue
        results = run_batch(batch, '%d_%s' % (rnd, loc))
        ok = 0
        for r in results:
            if r['ok']:
                ok += 1
                key = (r['ip'], r['port'])
                prev = alive.get(key)
                if not prev or (r['rtt_ms'] or 9999) < (prev['rtt_ms'] or 9999):
                    alive[key] = r
        print('  r%d %-5s %2d/%2d alive  (total unique %d)' % (rnd, loc, ok, len(batch), len(alive)))
        time.sleep(1)

nodes = sorted(alive.values(), key=lambda r: r['rtt_ms'] or 9999)
json.dump({'tested_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'rounds': ROUNDS,
           'alive': len(nodes), 'nodes': nodes},
          open(os.path.join(OUT, 'alive_nodes.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('\n==== 有效节点 %d ====' % len(nodes))
import collections
print('国家分布:', dict(collections.Counter(n['egress_country'] for n in nodes)))
for n in nodes:
    print('  %-16s %-5s %-4s→%-3s %6sms %s' % (n['ip'], n['port'], n['location'],
                                               n['egress_country'], n['rtt_ms'], n['egress_isp']))
