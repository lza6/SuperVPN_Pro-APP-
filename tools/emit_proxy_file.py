"""把存活节点写成 new-api 代理池可直读的文件（每行 socks5://127.0.0.1:PORT）

配合 mihomo_pool.py / pool_test.py 起的常驻 mihomo 使用：
  mihomo 的 listeners 已把每个 hysteria2 节点映射到 127.0.0.1:<base+i>，
  本脚本只负责产出对应端口清单，交给 new-api 的 TRYINGOPEN_PROXY_FILE。

用法:
  python emit_proxy_file.py                       # 默认 base 45000 -> proxies.txt
  python emit_proxy_file.py --base-port 45000 --out proxies.txt
  python emit_proxy_file.py --input recon/pool/livetest.json
"""
import argparse
import json
import os

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根

CANDIDATES = [
    ('recon/final/alive_nodes.json', 'nodes'),        # harvest_test.py 产出（推荐）
    ('recon/pool/livetest.json', 'results'),          # pool_test.py 产出
    ('recon/livetest/livetest.json', 'results'),
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--base-port', type=int, default=45000,
                   help='mihomo listeners 的起始 socks 端口（须与 mihomo_pool.py 一致）')
    p.add_argument('--out', default='proxies.txt')
    p.add_argument('--input', default=None, help='显式指定压测结果 JSON')
    p.add_argument('--all', action='store_true', help='不筛 ok，全量输出')
    return p.parse_args()


def main():
    args = parse_args()

    if args.input:
        srcs = [(args.input, None)]
    else:
        srcs = CANDIDATES

    picked, raw = None, None
    for rel, key in srcs:
        path = os.path.join(WORK, rel)
        if not os.path.exists(path):
            continue
        data = json.load(open(path, encoding='utf-8'))
        rows = data[key] if key else data.get('nodes') or data.get('results')
        if not rows:
            continue
        if args.input or any(r.get('ok') or r.get('egress_ip') for r in rows):
            picked, raw = rows, rel
            break
    if picked is None:
        raise SystemExit('没有找到压测结果，先跑 harvest_test.py 或 pool_test.py')

    nodes = picked if args.all else [
        r for r in picked if r.get('ok') or r.get('egress_ip')]
    lines = []
    for i, n in enumerate(nodes):
        port = n.get('socks_port') or (args.base_port + i)
        lines.append('socks5://127.0.0.1:%d' % port)

    out_path = os.path.join(WORK, args.out)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')

    # 附带一份元数据，便于人工核对端口与国家
    meta = [{'socks5': 'socks5://127.0.0.1:%d' % (n.get('socks_port') or args.base_port + i),
             'ip': n.get('ip'), 'port': n.get('port'),
             'country': n.get('egress_country'), 'isp': n.get('egress_isp'),
             'rtt_ms': n.get('rtt_ms')}
            for i, n in enumerate(nodes)]
    json.dump({'source': raw, 'base_port': args.base_port, 'count': len(meta),
               'proxies': meta},
              open(os.path.join(WORK, 'proxies.meta.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    print('source    :', raw)
    print('proxies   :', len(lines), '->', out_path)
    print('meta      : proxies.meta.json')
    print('示例      :', lines[:3])


if __name__ == '__main__':
    main()
