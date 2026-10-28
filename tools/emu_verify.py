"""模拟器内实测：宿主起 TCP->SOCKS5 转发，模拟器 adb shell 发起真实 HTTP

为什么这么做：
  用户要求实测必须在模拟器里做，不能借道本机 v2rayN(127.0.0.1:10808)。
  模拟器只能访问宿主的 127.0.0.1（映射为 10.0.2.2），而 Android shell 没有 SOCKS 客户端，
  所以这里在宿主开一个「裸 TCP 套接字 -> SOCKS5」转发器（只监听 127.0.0.1），
  然后从模拟器 adb shell 用 nc 发原始 HTTP 请求，出口 IP 由节点决定。

用法: python emu_verify.py [topN]
"""
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time

import yaml

from mihomo_pool import MIHOMO, RUNDIR, wait_port

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
ADB = r'D:\android-sdk\platform-tools\adb.exe'
DEV = 'emulator-5570'
OUT = os.path.join(WORK, 'recon', 'emu')
os.makedirs(OUT, exist_ok=True)

SOCKS_BASE = 47000
FWD_BASE = 48000


def start_mihomo(nodes, tag='emu'):
    cfg = {'log-level': 'warning', 'mode': 'global',
           'external-controller': '127.0.0.1:19700',
           'profile': {'store-selected': False, 'store-fake-ip': False},
           'proxies': [], 'listeners': [], 'rules': ['MATCH,DIRECT']}
    for i, n in enumerate(nodes):
        n['socks_port'] = SOCKS_BASE + i
        cfg['proxies'].append({'name': 'P%d' % i, 'type': 'hysteria2',
                               'server': n['ip'], 'port': n['port'],
                               'password': n['auth'], 'sni': n['sni'],
                               'skip-cert-verify': True,
                               'up': '100 Mbps', 'down': '200 Mbps'})
        cfg['listeners'].append({'name': 'li%d' % i, 'type': 'socks',
                                 'listen': '127.0.0.1', 'port': n['socks_port'],
                                 'proxy': 'P%d' % i, 'udp': True})
    path = os.path.join(RUNDIR, 'emu_%s.yaml' % tag)
    yaml.safe_dump(cfg, open(path, 'w', encoding='utf-8'), allow_unicode=True, sort_keys=False)
    log = open(os.path.join(RUNDIR, 'emu_%s.log' % tag), 'w')
    proc = subprocess.Popen([MIHOMO, '-d', RUNDIR, '-f', path], stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    wait_port(nodes[0]['socks_port'], 25)
    return proc, log


def socks5_connect(socks_host, socks_port, dst_host, dst_port, timeout=20):
    s = socket.create_connection((socks_host, socks_port), timeout=timeout)
    s.settimeout(timeout)
    s.sendall(b'\x05\x01\x00')
    if s.recv(2) != b'\x05\x00':
        raise RuntimeError('socks handshake failed')
    hb = dst_host.encode()
    s.sendall(b'\x05\x01\x00\x03' + bytes([len(hb)]) + hb + struct.pack('>H', dst_port))
    rep = s.recv(4)
    if len(rep) < 2 or rep[1] != 0:
        raise RuntimeError('socks connect failed %r' % (rep,))
    atyp = rep[3]
    if atyp == 1:
        s.recv(4)
    elif atyp == 3:
        n = s.recv(1)[0]
        s.recv(n)
    elif atyp == 4:
        s.recv(16)
    s.recv(2)
    return s


def forwarder(listen_port, socks_port):
    """裸 TCP -> SOCKS5（目标固定 ip-api.com:80）。已实测：模拟器经 10.0.2.2 可用。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', listen_port))
    srv.listen(8)

    def handle(cli):
        up = None
        try:
            up = socks5_connect('127.0.0.1', socks_port, 'ip-api.com', 80, timeout=25)
            cli.settimeout(30)
            req = cli.recv(65536)
            if req:
                up.sendall(req)
                while True:
                    chunk = up.recv(65536)
                    if not chunk:
                        break
                    cli.sendall(chunk)
        except Exception:
            pass
        finally:
            for x in (cli, up):
                if x is None:
                    continue
                try:
                    x.close()
                except Exception:
                    pass

    def loop():
        while True:
            try:
                cli, _ = srv.accept()
            except Exception:
                return
            threading.Thread(target=handle, args=(cli,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return srv


def adb_http(port, timeout=30):
    """在模拟器里用 nc 发原始 HTTP 请求，返回 (body, stderr)"""
    cmd = ("printf 'GET /json/?fields=query,countryCode,isp HTTP/1.0\\r\\n"
           "Host: ip-api.com\\r\\n\\r\\n' | nc -q 6 -w 30 10.0.2.2 %d" % port)
    p = subprocess.run([ADB, '-s', DEV, 'shell', cmd], capture_output=True, text=True,
                       timeout=timeout)
    out = p.stdout or ''
    mt = re.search(r'\{[^{}]*\}', out)
    return (mt.group(0) if mt else out.strip()[-200:]), (p.stderr or '')[:120]


def main():
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    nodes = json.load(open(os.path.join(WORK, 'recon', 'final', 'alive_nodes.json'),
                           encoding='utf-8'))['nodes'][:top]
    print('[i] 模拟器内实测 %d 个节点' % len(nodes))

    # 基线：不加代理时模拟器看到的出口 IP
    base, _ = adb_http_direct()
    print('[i] 模拟器裸出口:', base)

    proc, log = start_mihomo(nodes)
    servers = []
    try:
        for i, n in enumerate(nodes):
            servers.append(forwarder(FWD_BASE + i, n['socks_port']))
        time.sleep(1.5)
        rows = []
        for i, n in enumerate(nodes):
            note = ''
            try:
                body, err = adb_http(FWD_BASE + i)
                info = json.loads(body) if body.startswith('{') else {}
            except Exception as exc:
                info, note = {}, type(exc).__name__
            got = info.get('query')
            ok = bool(got) and got == n['ip']
            rows.append({'ip': n['ip'], 'port': n['port'], 'country': n['egress_country'],
                         'emu_egress': got, 'match': ok, 'error': note,
                         'isp': (info.get('isp') or '')[:34]})
            print('%s %-16s %-4s 模拟器出口=%-16s %s' % (
                'OK  ' if ok else 'FAIL', n['ip'], n['egress_country'],
                got or '-', note or rows[-1]['isp']))
        json.dump({'tested_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'baseline_egress': base, 'device': DEV, 'results': rows},
                  open(os.path.join(OUT, 'emu_verify.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print('\n模拟器内真实转发成功 %d / %d' % (sum(1 for r in rows if r['match']), len(rows)))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
        log.close()


def adb_http_direct():
    cmd = ("printf 'GET /json/?fields=query,countryCode,isp HTTP/1.0\\r\\n"
           "Host: ip-api.com\\r\\n\\r\\n' | nc -q 6 -w 30 ip-api.com 80")
    p = subprocess.run([ADB, '-s', DEV, 'shell', cmd], capture_output=True, text=True,
                       timeout=30)
    out = p.stdout or ''
    mt = re.search(r'\{[^{}]*\}', out)
    try:
        return json.loads(mt.group(0)).get('query'), out
    except Exception:
        return out.strip()[-120:], out


if __name__ == '__main__':
    main()
