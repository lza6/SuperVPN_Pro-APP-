"""SuperVPN Pro 全地区节点收割：注册/复用账号 -> location.json -> acquire.json(逐地区) -> 汇总"""
import json
import os
import time

import supervpn_client as sc

OUT = 'recon/harvest'
os.makedirs(OUT, exist_ok=True)

ACC_FILE = 'recon/account.json'

if os.path.exists(ACC_FILE):
    saved = json.load(open(ACC_FILE, encoding='utf-8'))
else:
    saved = None

if saved:
    acc = sc.SuperVpnAccount(device_id=saved['device_id'], username=saved['username'])
    acc.user = saved['user']
    acc.gateway = saved.get('gateway')
    acc.app_version_code = saved.get('app_version_code', '209')
    print('[i] reuse account', acc.username, 'gw', acc.gateway)
else:
    acc = sc.SuperVpnAccount()
    reg = acc.register()
    if reg.get('status') != 0:
        raise RuntimeError('register failed: %r' % reg)
    acc.user = reg['data']['user']
    saved = {'username': acc.username, 'device_id': acc.device_id,
             'user': acc.user, 'gateway': acc.gateway,
             'app_version_code': acc.app_version_code}
    json.dump(saved, open(ACC_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[i] registered', acc.username, 'pwd', acc.user.get('password'))

# 先试 location 列表，失败则重注册
try:
    loc = acc.locations()
    if loc.get('status') != 0:
        raise RuntimeError(loc)
except Exception as exc:
    print('[warn] location failed, re-register:', exc)
    acc = sc.SuperVpnAccount()
    reg = acc.register()
    acc.user = reg['data']['user']
    json.dump({'username': acc.username, 'device_id': acc.device_id,
               'user': acc.user, 'gateway': acc.gateway,
               'app_version_code': acc.app_version_code},
              open(ACC_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    loc = acc.locations()

locations = [x['location'] for x in loc['data']['locations']]
print('[i] locations:', locations)

inventory = {'account': {'username': acc.username, 'password': acc.user.get('password'),
                         'device_id': acc.device_id, 'gateway': acc.gateway},
             'harvested_at': time.strftime('%Y-%m-%d %H:%M:%S'),
             'locations': {}}

for name in locations:
    for attempt in range(3):
        try:
            res = acc.acquire(name)
            break
        except Exception as exc:
            print('  [retry %d] %s: %s' % (attempt + 1, name, exc))
            time.sleep(2)
            res = None
    if not res or res.get('status') != 0:
        print('  [fail]', name, res)
        continue
    data = res['data']
    json.dump(res, open(os.path.join(OUT, 'acquire_%s.json' % name), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    def slim(arr):
        out = []
        for node in arr or []:
            out.append({'ip': node.get('ip'), 'location': node.get('location'),
                        'port': node.get('port'), 'gwType': node.get('gwType'),
                        'psk': node.get('psk'),
                        'has_cert': (node.get('cert') or '') != 'none',
                        'config': node.get('config') or {}})
        return out

    inventory['locations'][name] = {
        'userIp': data.get('userIp'), 'geo': data.get('geo'),
        'configVersion': data.get('configVersion'),
        'ikev2': slim(data.get('gateways')),
        'relay': slim(data.get('relayGateways')),
        'tcp': slim(data.get('tcpGateways')) + slim(data.get('tcpGatewaysBack')),
        'hy2': slim(data.get('hy2Gateways')) + slim(data.get('hy2GatewaysBack')),
        'configHy2': data.get('configHy2'),
        'configTun': data.get('configTun'),
    }
    n = inventory['locations'][name]
    print('  %-5s ikev2=%2d tcp=%2d hy2=%2d cv=%s' % (
        name, len(n['ikev2']), len(n['tcp']), len(n['hy2']), n['configVersion']))
    time.sleep(1)

json.dump(inventory, open(os.path.join(OUT, 'inventory.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)

total = {'ikev2': 0, 'tcp': 0, 'hy2': 0}
uniq = {'ikev2': set(), 'tcp': set(), 'hy2': set()}
for name, n in inventory['locations'].items():
    for k in total:
        for node in n[k]:
            uniq[k].add((node['ip'], node['port']))
        total[k] += len(n[k])
print('[done] 记录数', total, '去重后', {k: len(v) for k, v in uniq.items()})
