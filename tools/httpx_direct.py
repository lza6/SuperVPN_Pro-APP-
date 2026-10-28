"""直连 HTTP 客户端：DoH 解析 + 直连 IP（完全绕过本机 HTTP_PROXY / 被污染的本地 DNS）"""
import socket, ssl, json, urllib.request, gzip, zlib, io

DOH = 'https://223.5.5.5/resolve'
_cache = {}

def doh(name):
    if name in _cache: return _cache[name]
    ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
    u = f'{DOH}?name={name}&type=A'
    r = op.open(urllib.request.Request(u, headers={'User-Agent':'curl/8'}), timeout=8)
    d = json.loads(r.read())
    ips = [a['data'] for a in d.get('Answer',[]) if a.get('type')==1]
    _cache[name] = ips
    return ips

def raw(host, path='/', method='GET', headers=None, body=None, timeout=12, port=443, sni=None, ip=None, tls=True):
    ips = [ip] if ip else (doh(host) if not host.replace('.','').isdigit() else [host])
    if not ips: raise RuntimeError('DNS fail '+host)
    last = None
    for a in ips:
        try:
            s = socket.create_connection((a, port), timeout=timeout)
            if tls:
                ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
                s = ctx.wrap_socket(s, server_hostname=sni or host)
            h = {'Host': host, 'User-Agent':'okhttp/4.12.0', 'Accept-Encoding':'identity', 'Connection':'close'}
            h.update(headers or {})
            req = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k,v in h.items()) + '\r\n'
            s.sendall(req.encode() + (body or b''))
            buf = b''
            while True:
                c = s.recv(65536)
                if not c: break
                buf += c
            s.close()
            head, _, payload = buf.partition(b'\r\n\r\n')
            lines = head.decode('latin1').split('\r\n')
            status = int(lines[0].split()[1]) if len(lines[0].split())>1 else 0
            hdrs = {}
            for l in lines[1:]:
                if ':' in l:
                    k,v = l.split(':',1); hdrs[k.strip().lower()] = v.strip()
            if hdrs.get('transfer-encoding','').lower() == 'chunked':
                out=b''; rest=payload
                while True:
                    i = rest.find(b'\r\n')
                    if i<0: break
                    try: n=int(rest[:i].split(b';')[0],16)
                    except: break
                    if n==0: break
                    out += rest[i+2:i+2+n]; rest = rest[i+2+n+2:]
                payload = out
            if hdrs.get('content-encoding')=='gzip':
                payload = gzip.decompress(payload)
            return status, hdrs, payload
        except Exception as e:
            last = e
    raise last
