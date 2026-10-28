"""订阅产物自检：把 output/sub_hy2_alive.txt 里的 URI 反向解析成 mihomo 配置，
真实连一次，确认「链接格式」本身没写错（不是只验证内存里的对象）。"""
import json
import os
import subprocess
import sys
import time
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from mihomo_pool import MIHOMO, RUNDIR, test_one, wait_port

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
SUB = os.path.join(WORK, 'output', 'sub_hy2_alive.txt')
SOCKS_BASE = 49000


def parse_uri(uri):
    u = urlparse(uri)
    assert u.scheme == 'hysteria2', u.scheme
    auth = unquote(u.username or '') + ':' + unquote(u.password or '') \
        if u.password else unquote(u.username or '')
    # userinfo 整体 percent-encode，可能出现 'user:pass@ts@hash' 全在 username 里
    raw_userinfo = uri.split('://', 1)[1].rsplit('@', 1)[0]
    auth = unquote(raw_userinfo)
    q = parse_qs(u.query)
    return {'server': u.hostname, 'port': u.port, 'password': auth,
            'sni': (q.get('sni') or ['centos.org'])[0],
            'insecure': (q.get('insecure') or ['1'])[0] == '1'}


def main():
    uris = [l.strip() for l in open(SUB, encoding='utf-8') if l.strip()]
    print('订阅条数', len(uris))
    sample = uris[:5]

    cfg = {'log-level': 'warning', 'mode': 'global',
           'external-controller': '127.0.0.1:19800',
           'profile': {'store-selected': False, 'store-fake-ip': False},
           'proxies': [], 'listeners': [], 'rules': ['MATCH,DIRECT']}
    nodes = []
    for i, uri in enumerate(sample):
        p = parse_uri(uri)
        cfg['proxies'].append({'name': 'U%d' % i, 'type': 'hysteria2',
                               'server': p['server'], 'port': p['port'],
                               'password': p['password'], 'sni': p['sni'],
                               'skip-cert-verify': p['insecure'],
                               'up': '100 Mbps', 'down': '200 Mbps'})
        cfg['listeners'].append({'name': 'ul%d' % i, 'type': 'socks',
                                 'listen': '127.0.0.1', 'port': SOCKS_BASE + i,
                                 'proxy': 'U%d' % i, 'udp': True})
        nodes.append({'idx': i, 'ip': p['server'], 'port': p['port'],
                      'socks_port': SOCKS_BASE + i,
                      'location': '-', 'sni': p['sni'], 'auth': p['password']})
        print('解析 %-16s:%-5d auth=%s...%s sni=%s' % (
            p['server'], p['port'], p['password'][:22], p['password'][-8:], p['sni']))

    path = os.path.join(RUNDIR, 'uri_check.yaml')
    yaml.safe_dump(cfg, open(path, 'w', encoding='utf-8'), allow_unicode=True, sort_keys=False)
    log = open(os.path.join(RUNDIR, 'uri_check.log'), 'w')
    proc = subprocess.Popen([MIHOMO, '-d', RUNDIR, '-f', path], stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        if not wait_port(SOCKS_BASE, 25):
            print('mihomo 未起来'); print(open(os.path.join(RUNDIR, 'uri_check.log'), encoding='utf-8', errors='replace').read()[-1200:]); return
        ok = 0
        for n in nodes:
            r = test_one(n, timeout=20)
            good = bool(r['egress_ip'])
            ok += 1 if good else 0
            print('%s %-16s 出口=%s' % ('OK  ' if good else 'FAIL', n['ip'], r['egress_ip'] or r['error']))
        print('\nURI 订阅可用 %d / %d' % (ok, len(nodes)))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
        log.close()


if __name__ == '__main__':
    main()
