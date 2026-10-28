"""QUIC 存活探测（无依赖）

Hysteria2 是纯 QUIC/UDP 协议。给 UDP 端口发一个「未知版本」的 long-header 包，
按 RFC 9000 §6 服务端必须回 Version Negotiation（version=0）。回了 = 该端口确实在跑 QUIC。
"""
import json
import os
import random
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
OUT = os.path.join(WORK, 'recon', 'quicprobe')
os.makedirs(OUT, exist_ok=True)


def build_vn_trigger():
    dcid = bytes(random.getrandbits(8) for _ in range(8))
    scid = bytes(random.getrandbits(8) for _ in range(8))
    return bytes([0xC0]) + struct.pack('>I', 0x1A2A3A4A) + bytes([len(dcid)]) + dcid + bytes([len(scid)]) + scid


def probe(ip, port=443, timeout=2.0, tries=2):
    pkt = build_vn_trigger()
    t0 = time.time()
    for _ in range(tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(pkt, (ip, port))
            data, _ = s.recvfrom(2048)
        except socket.timeout:
            s.close()
            continue
        except Exception as exc:
            s.close()
            return {'alive': False, 'error': type(exc).__name__, 'rtt_ms': None}
        finally:
            try:
                s.close()
            except Exception:
                pass
        rtt = int((time.time() - t0) * 1000)
        if len(data) >= 5:
            version = struct.unpack('>I', data[1:5])[0]
            if version == 0:
                return {'alive': True, 'kind': 'version_negotiation', 'rtt_ms': rtt,
                        'reply_len': len(data)}
            return {'alive': True, 'kind': 'long_header_v%d' % version, 'rtt_ms': rtt,
                    'reply_len': len(data)}
        return {'alive': True, 'kind': 'unknown', 'rtt_ms': rtt, 'reply_len': len(data)}
    return {'alive': False, 'error': 'timeout', 'rtt_ms': None}


def main():
    inv = json.load(open(os.path.join(WORK, 'recon', 'harvest', 'inventory.json'), encoding='utf-8'))
    nodes, seen = [], set()
    for loc, data in inv['locations'].items():
        for node in data['hy2']:
            key = (node['ip'], node['port'])
            if key in seen:
                continue
            seen.add(key)
            nodes.append({'ip': node['ip'], 'port': node['port'], 'location': node['location']})
    print('probing', len(nodes), 'hy2 endpoints')

    def job(node):
        r = probe(node['ip'], node['port'])
        r.update(node)
        print('%s %-16s:%-5d %-4s %-22s rtt=%s' % (
            'UP  ' if r['alive'] else 'DOWN', node['ip'], node['port'], node['location'],
            r.get('kind') or r.get('error'), r.get('rtt_ms')))
        return r

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(job, nodes))

    alive = [r for r in results if r['alive']]
    json.dump({'tested_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'total': len(results),
               'alive': len(alive), 'results': results},
              open(os.path.join(OUT, 'quic_alive.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\nQUIC alive %d / %d' % (len(alive), len(results)))


if __name__ == '__main__':
    main()
