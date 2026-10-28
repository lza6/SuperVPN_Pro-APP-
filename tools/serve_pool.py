"""常驻代理池：把 recon/final/alive_nodes.json 的存活节点映射成 127.0.0.1:45000+i 的 SOCKS5

这是给「外部程序（new-api / 浏览器 / 脚本）」用的常驻入口：
  python serve_pool.py            # 前台常驻，Ctrl+C 退出
  python serve_pool.py --daemon   # 交给 Start-Process 常驻（见 README）

只监听 127.0.0.1，不改系统代理，不占用 v2rayN 的 10808/10809。
"""
import argparse
import json
import os
import subprocess
import sys
import time

import yaml

from mihomo_pool import MIHOMO, RUNDIR, wait_port

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
BASE_PORT = 45000


def build(nodes, path):
    cfg = {'log-level': 'warning', 'mode': 'global',
           'external-controller': '127.0.0.1:19099',
           'profile': {'store-selected': False, 'store-fake-ip': False},
           'proxies': [], 'listeners': [], 'rules': ['MATCH,DIRECT']}
    for i, n in enumerate(nodes):
        n['socks_port'] = BASE_PORT + i
        cfg['proxies'].append({'name': 'SVPN-%d' % i, 'type': 'hysteria2',
                               'server': n['ip'], 'port': n['port'],
                               'password': n['auth'], 'sni': n['sni'],
                               'skip-cert-verify': True,
                               'up': '100 Mbps', 'down': '200 Mbps'})
        cfg['listeners'].append({'name': 'pool-%d' % i, 'type': 'socks',
                                 'listen': '127.0.0.1', 'port': BASE_PORT + i,
                                 'proxy': 'SVPN-%d' % i, 'udp': True})
    yaml.safe_dump(cfg, open(path, 'w', encoding='utf-8'), allow_unicode=True, sort_keys=False)
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base-port', type=int, default=BASE_PORT)
    args = ap.parse_args()

    src = os.path.join(WORK, 'recon', 'final', 'alive_nodes.json')
    if not os.path.exists(src):
        raise SystemExit('缺 %s，先跑 harvest_test.py' % src)
    nodes = json.load(open(src, encoding='utf-8'))['nodes']
    print('[i] 节点', len(nodes))

    cfg_path = os.path.join(RUNDIR, 'serve.yaml')
    build(nodes, cfg_path)
    log = open(os.path.join(RUNDIR, 'serve.log'), 'w')
    proc = subprocess.Popen([MIHOMO, '-d', RUNDIR, '-f', cfg_path], stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    print('[i] mihomo pid', proc.pid)
    if not wait_port(args.base_port, 25):
        print('[fail] 起不来'); print(open(os.path.join(RUNDIR, 'serve.log'),
                                          encoding='utf-8', errors='replace').read()[-1500:])
        proc.terminate()
        return

    # 回写端口清单（保留原文件的 tested_at/alive 等字段，别把统计信息冲掉）
    raw = json.load(open(src, encoding='utf-8'))
    for i, n in enumerate(nodes):
        n['socks_port'] = args.base_port + i
    raw['nodes'] = nodes
    raw['count'] = len(nodes)
    raw['served_base_port'] = args.base_port
    raw['served_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    json.dump(raw, open(src, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    lines = ['socks5://127.0.0.1:%d' % (args.base_port + i) for i in range(len(nodes))]
    open(os.path.join(WORK, 'proxies.txt'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('[i] SOCKS5 端口 %d..%d 已就绪；proxies.txt 已更新'
          % (args.base_port, args.base_port + len(nodes) - 1))
    print('[i] 自检:  curl -x socks5h://127.0.0.1:%d http://ip-api.com/json' % args.base_port)

    try:
        while True:
            if proc.poll() is not None:
                print('[!] mihomo 退出 code=%s' % proc.returncode)
                break
            time.sleep(5)
    except KeyboardInterrupt:
        print('\n[i] 退出')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
        log.close()


if __name__ == '__main__':
    main()
