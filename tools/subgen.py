"""订阅产物生成：hysteria2 URI / Clash.Meta / sing-box / new-api 本地 SOCKS5 清单"""
import base64
import json
import os
from urllib.parse import quote

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
OUT = os.path.join(WORK, 'output')
os.makedirs(OUT, exist_ok=True)


def load():
    """以 recon/final/alive_nodes.json（收割即测产出）为主源；
    老数据（recon/harvest/inventory.json）只在没有 final 时兜底。"""
    emu = {}
    efp = os.path.join(WORK, 'recon', 'emu', 'emu_verify.json')
    if os.path.exists(efp):
        for r in json.load(open(efp, encoding='utf-8'))['results']:
            emu[(r['ip'], r['port'])] = r['match']

    final_fp = os.path.join(WORK, 'recon', 'final', 'alive_nodes.json')
    inv_fp = os.path.join(WORK, 'recon', 'harvest', 'inventory.json')
    account = {}
    for fp in (os.path.join(WORK, 'recon', 'account.json'),):
        if os.path.exists(fp):
            a = json.load(open(fp, encoding='utf-8'))
            account = {'username': a.get('username'),
                       'password': (a.get('user') or {}).get('password'),
                       'device_id': a.get('device_id'), 'gateway': a.get('gateway')}

    nodes, seen = [], set()

    def push(ip, port, location, sni, auth, ok, rtt, ctry, isp, socks, err):
        key = (ip, port)
        if key in seen:
            return
        seen.add(key)
        nodes.append({
            'tag': 'SVPN-%s-%s' % ((ctry or location or 'XX').upper(), ip.replace('.', '_')),
            'ip': ip, 'port': port, 'location': location,
            'sni': sni or 'centos.org', 'auth': auth,
            'alive': ok, 'emu_verified': bool(emu.get(key)),
            'rtt_ms': rtt, 'egress_ip': ip, 'egress_country': ctry,
            'egress_isp': isp, 'socks_port': socks, 'error': err,
        })

    if os.path.exists(final_fp):
        for r in json.load(open(final_fp, encoding='utf-8'))['nodes']:
            push(r['ip'], r['port'], r.get('location'), r.get('sni'), r['auth'],
                 True, r.get('rtt_ms'), r.get('egress_country'), r.get('egress_isp'),
                 r.get('socks_port'), None)
        return account, nodes

    inv = json.load(open(inv_fp, encoding='utf-8'))
    for loc, data in inv['locations'].items():
        for node in data['hy2']:
            cfg = node.get('config') or {}
            push(node['ip'], node['port'], node['location'],
                 cfg.get('@HY2_DOMAIN'), cfg.get('@HY2_AUTH', ''), False, None, None, None, None, None)
    return inv.get('account', account), nodes


def hy2_uri(node):
    auth = quote(node['auth'], safe='')
    name = quote('%s %s %s' % (node['tag'], node['ip'], node['location'].upper()), safe='')
    return 'hysteria2://%s@%s:%d/?sni=%s&insecure=1#%s' % (
        auth, node['ip'], node['port'], node['sni'], name)


def clash_yaml(nodes):
    lines = ['# SuperVPN Pro 2.0.9 -> Clash.Meta / mihomo', 'proxies:']
    for n in nodes:
        lines += [
            '  - name: "%s"' % n['tag'],
            '    type: hysteria2',
            '    server: %s' % n['ip'],
            '    port: %d' % n['port'],
            '    password: "%s"' % n['auth'],
            '    sni: %s' % n['sni'],
            '    skip-cert-verify: true',
            '    up: "50 Mbps"',
            '    down: "200 Mbps"',
        ]
    lines += ['', 'proxy-groups:', '  - name: "SVPN"', '    type: select', '    proxies:']
    lines += ['      - "%s"' % n['tag'] for n in nodes]
    return '\n'.join(lines) + '\n'


def singbox_json(nodes):
    out = []
    for n in nodes:
        out.append({
            'type': 'hysteria2', 'tag': n['tag'],
            'server': n['ip'], 'server_port': n['port'],
            'password': n['auth'],
            'tls': {'enabled': True, 'server_name': n['sni'], 'insecure': True},
        })
    return json.dumps({'outbounds': out}, ensure_ascii=False, indent=2)


def main():
    account, nodes = load()
    json.dump({'account': account, 'count': len(nodes), 'nodes': nodes},
              open(os.path.join(OUT, 'nodes_hy2.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    uris = [hy2_uri(n) for n in nodes]
    open(os.path.join(OUT, 'sub_hy2.txt'), 'w', encoding='utf-8').write('\n'.join(uris) + '\n')
    open(os.path.join(OUT, 'sub_hy2_base64.txt'), 'w', encoding='utf-8').write(
        base64.b64encode(('\n'.join(uris) + '\n').encode()).decode())

    alive = [n for n in nodes if n['alive']]
    if alive:
        auris = [hy2_uri(n) for n in alive]
        open(os.path.join(OUT, 'sub_hy2_alive.txt'), 'w', encoding='utf-8').write('\n'.join(auris) + '\n')
        open(os.path.join(OUT, 'sub_hy2_alive_base64.txt'), 'w', encoding='utf-8').write(
            base64.b64encode(('\n'.join(auris) + '\n').encode()).decode())
        open(os.path.join(OUT, 'clash_hy2_alive.yaml'), 'w', encoding='utf-8').write(clash_yaml(alive))
        open(os.path.join(OUT, 'singbox_hy2_alive.json'), 'w', encoding='utf-8').write(singbox_json(alive))

    open(os.path.join(OUT, 'clash_hy2.yml'), 'w', encoding='utf-8').write(clash_yaml(nodes))
    open(os.path.join(OUT, 'singbox_hy2.json'), 'w', encoding='utf-8').write(singbox_json(nodes))

    print('nodes', len(nodes), 'alive', len(alive))
    print('outputs ->', OUT)
    for f in sorted(os.listdir(OUT)):
        print('  ', f, os.path.getsize(os.path.join(OUT, f)), 'B')


if __name__ == '__main__':
    main()
