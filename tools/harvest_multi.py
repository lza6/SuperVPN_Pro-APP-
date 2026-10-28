"""多轮 acquire 收割（节点列表服务端会轮换，单轮只能拿到 8 个）

对每个地区重复 acquire N 次，汇总所有出现过的 hy2 节点，
每个节点保留「本轮新鲜凭证」，最后统一压测。
"""
import json
import os
import sys
import time

import supervpn_client as sc

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
OUT = os.path.join(WORK, 'recon', 'harvest')
os.makedirs(OUT, exist_ok=True)

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 4
GAP = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

acct = json.load(open(os.path.join(WORK, 'recon', 'account.json'), encoding='utf-8'))
acc = sc.SuperVpnAccount(device_id=acct['device_id'], username=acct['username'])
acc.user = acct['user']
acc.gateway = acct.get('gateway')

loc = acc.locations()
locations = [x['location'] for x in loc['data']['locations']]
print('[i] locations', locations, '| rounds', ROUNDS)

pool = {}          # (ip,port) -> node dict (最新凭证)
per_loc = {}
raw = []
for rnd in range(ROUNDS):
    for name in locations:
        try:
            res = acc.acquire(name)
        except Exception as exc:
            print('  [warn] %s r%d %s' % (name, rnd, exc))
            continue
        if res.get('status') != 0:
            print('  [warn] %s r%d status=%s %s' % (name, rnd, res.get('status'), res.get('msg')))
            continue
        data = res['data']
        raw.append({'round': rnd, 'location': name, 'data': data})
        hy2 = (data.get('hy2Gateways') or []) + (data.get('hy2GatewaysBack') or [])
        per_loc.setdefault(name, set())
        for node in hy2:
            cfg = node.get('config') or {}
            key = (node['ip'], node['port'])
            per_loc[name].add(key)
            pool[key] = {
                'ip': node['ip'], 'port': node['port'],
                'location': node['location'] or name,
                'sni': cfg.get('@HY2_DOMAIN') or 'centos.org',
                'auth': cfg.get('@HY2_AUTH', ''),
                'from_request': name, 'round': rnd,
            }
        print('  r%d %-5s hy2=%-2d cumulative=%-3d' % (rnd, name, len(hy2), len(pool)))
        time.sleep(GAP)

nodes = list(pool.values())
json.dump({'harvested_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'rounds': ROUNDS,
           'count': len(nodes), 'nodes': nodes,
           'per_location': {k: len(v) for k, v in per_loc.items()}},
          open(os.path.join(OUT, 'hy2_pool.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
json.dump({'harvested_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'raw': raw},
          open(os.path.join(OUT, 'acquire_all.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('[done] unique hy2 nodes', len(nodes))
