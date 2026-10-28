"""失败节点二次验证：用「该节点所在地区」的新鲜 acquire 凭证重测，判定是节点死还是凭证不匹配"""
import json
import os
import subprocess
import sys
import time

import yaml

import supervpn_client as sc
from mihomo_pool import RUNDIR, MIHOMO, build_config, wait_port, test_one, SOCKS_BASE

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
acct = json.load(open(os.path.join(WORK, 'recon', 'account.json'), encoding='utf-8'))
acc = sc.SuperVpnAccount(device_id=acct['device_id'], username=acct['username'])
acc.user = acct['user']
acc.gateway = acct.get('gateway')

live = json.load(open(os.path.join(RUNDIR, 'livetest.json'), encoding='utf-8'))
failed = [r for r in live['results'] if not r['ok']]
print('failed', len(failed))

locs = sorted({r['location'] for r in failed})
fresh = {}
for loc in locs:
    try:
        res = acc.acquire(loc)
        fresh[loc] = res['data']
        print('[i] fresh acquire', loc, 'hy2=',
              len(res['data'].get('hy2Gateways') or []))
    except Exception as exc:
        print('[warn] acquire', loc, exc)
    time.sleep(1)

# 用新鲜凭证重建待测列表
retry_nodes = []
for r in failed:
    data = fresh.get(r['location'])
    if not data:
        continue
    match = None
    for node in (data.get('hy2Gateways') or []):
        if node['ip'] == r['ip']:
            match = node
            break
    if not match:
        print('[i] node gone from', r['location'], ':', r['ip'])
        continue
    cfg = match.get('config') or {}
    retry_nodes.append({
        'idx': len(retry_nodes), 'name': 'RETRY-%s' % r['ip'].replace('.', '_'),
        'ip': r['ip'], 'port': r['port'], 'location': r['location'],
        'sni': cfg.get('@HY2_DOMAIN') or 'centos.org',
        'auth': cfg.get('@HY2_AUTH', ''),
        'socks_port': SOCKS_BASE + 200 + len(retry_nodes),
        'old_auth': r.get('auth'),
    })
print('retry candidates', len(retry_nodes))
if not retry_nodes:
    sys.exit(0)

cfg_path = os.path.join(RUNDIR, 'retry_config.yaml')
build_config(retry_nodes, cfg_path)
log = open(os.path.join(RUNDIR, 'mihomo_retry.log'), 'w')
proc = subprocess.Popen([MIHOMO, '-d', RUNDIR, '-f', cfg_path], stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
print('mihomo retry pid', proc.pid)
try:
    wait_port(retry_nodes[0]['socks_port'], 25)
    out = []
    for n in retry_nodes:
        r = test_one(n, timeout=18)
        r['auth_fixed'] = (r.get('old_auth') != n['auth'])
        print('%s %-16s %-4s %6sms %-16s %s' % (
            'OK  ' if r['ok'] else 'FAIL', r['ip'], r['location'], r['rtt_ms'],
            r['egress_ip'] or '-', r['egress_isp'] or r['error'] or ''))
        out.append(r)
    json.dump(out, open(os.path.join(RUNDIR, 'retest.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\nretry alive', sum(1 for r in out if r['ok']), '/', len(out))
finally:
    log.close()
