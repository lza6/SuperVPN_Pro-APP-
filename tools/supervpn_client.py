"""
SuperVPN Pro (com.jrzheng.supervpnpayment) 客户端协议复现

协议要点（逆向自 v2.0.9）:
  POST https://<gateway><path>   Content-Type: application/json
  body = Base64( DES/ECB/PKCS5Padding( JSON ) )
  DES key = Base64("6c" + "GDH" + "63lEyY=")   -> ub0.<clinit>
  JSON   = { "<md5(uuid)>":"<md5(uuid)>" 随机噪声, ...参数..., "path": "<path>" }
  响应   = Base64(DES 密文) -> {"status":..,"msg":..,"data":{..}}

网关发现源（w9.a / w9.b / w9.c 三个内置字符串 + DoH getip.supervpn.cc）
"""

import base64
import gzip
import hashlib
import json
import random
import socket
import ssl
import urllib.request
import uuid

from Crypto.Cipher import DES

DES_KEY = base64.b64decode("6c" + "GDH" + "63lEyY=")  # e9c1831fade51326

DOH = 'https://223.5.5.5/resolve'
_dns = {}


def doh(name):
    if name in _dns:
        return _dns[name]
    if name.replace('.', '').isdigit():
        return [name]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(DOH + '?name=' + name + '&type=A',
                                 headers={'User-Agent': 'curl/8'})
    data = json.loads(op.open(req, timeout=8).read())
    ips = [a['data'] for a in data.get('Answer', []) if a.get('type') == 1]
    _dns[name] = ips
    return ips


def _raw(host, path, method, body, headers, timeout=12, port=443):
    ips = doh(host)
    if not ips:
        raise RuntimeError('DNS fail ' + host)
    last = None
    for ip in ips:
        try:
            s = socket.create_connection((ip, port), timeout=timeout)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            s = ctx.wrap_socket(s, server_hostname=host)
            h = {'Host': host, 'User-Agent': 'okhttp/4.12.0',
                 'Connection': 'close', 'Accept-Encoding': 'identity'}
            h.update(headers)
            req = method + ' ' + path + ' HTTP/1.1\r\n'
            req += ''.join(k + ': ' + v + '\r\n' for k, v in h.items())
            req += '\r\n'
            s.sendall(req.encode() + (body or b''))
            buf = b''
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            s.close()
            head, _, payload = buf.partition(b'\r\n\r\n')
            lines = head.decode('latin1').split('\r\n')
            status = int(lines[0].split()[1])
            hd = {}
            for line in lines[1:]:
                if ':' in line:
                    k, v = line.split(':', 1)
                    hd[k.strip().lower()] = v.strip()
            if hd.get('transfer-encoding', '').lower() == 'chunked':
                out = b''
                rest = payload
                while True:
                    i = rest.find(b'\r\n')
                    if i < 0:
                        break
                    n = int(rest[:i].split(b';')[0], 16)
                    if n == 0:
                        break
                    out += rest[i + 2:i + 2 + n]
                    rest = rest[i + 2 + n + 2:]
                payload = out
            if hd.get('content-encoding') == 'gzip':
                payload = gzip.decompress(payload)
            return status, hd, payload
        except Exception as exc:  # 换下一个 IP 重试
            last = exc
    raise last


def md5(text):
    return hashlib.md5(text.encode()).hexdigest()


def des_encrypt(plain):
    pad = 8 - len(plain.encode()) % 8
    data = plain.encode() + bytes([pad]) * pad
    return base64.b64encode(DES.new(DES_KEY, DES.MODE_ECB).encrypt(data)).decode()


def des_decrypt(b64):
    raw = DES.new(DES_KEY, DES.MODE_ECB).decrypt(base64.b64decode(b64))
    pad = raw[-1]
    return raw[:-pad].decode('utf-8', 'replace') if 0 < pad <= 8 else raw.decode('utf-8', 'replace')


def build_body(path, params):
    payload = {}
    nk, nv = md5(str(uuid.uuid4())), md5(str(uuid.uuid4()))
    payload[nk] = nv
    for k, v in params.items():
        payload[k] = v
        if random.getrandbits(1) == 0:
            payload[nk] = nv
    if random.getrandbits(1) == 0:
        payload[nk] = nv
    payload['path'] = path
    payload[nk] = nv
    return des_encrypt(json.dumps(payload, separators=(',', ':'))).encode()


def api(host, path, params, timeout=15, verbose=False):
    """注意：真实请求一律 POST 到 https://<gateway>/ （根路径），
    API 路径只放在加密体的 "path" 字段里。"""
    body = build_body(path, params)
    status, hd, payload = _raw(host, '/', 'POST', body, {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': str(len(body)),
    }, timeout=timeout)
    if verbose:
        print('  HTTP', status, hd.get('content-type'), len(payload), 'B')
    if status != 200:
        raise RuntimeError('HTTP %d: %r' % (status, payload[:200]))
    text = payload.decode().strip()
    if text.startswith('{'):      # 服务端错误响应是明文 JSON
        return json.loads(text)
    return json.loads(des_decrypt(text))


def fetch_gateways(timeout=10, verbose=True):
    sources = [('kkio778.pages.dev', '/'),
               ('d1ol9zp47zx5tq.cloudfront.net', '/index.html'),
               ('cdn.statically.io', '/gh/HiSuperSoft/JustTest/main/servers.txt')]
    out = []
    for host, path in sources:
        try:
            status, _, body = _raw(host, path, 'GET', None, {}, timeout=timeout)
            if status == 200:
                for line in body.decode('utf-8', 'replace').split('\n'):
                    line = line.strip()
                    if line and not line.startswith('<') and len(line) < 80:
                        out.append(line)
        except Exception as exc:
            if verbose:
                print('  [warn] gateway source', host + ':', type(exc).__name__)
    try:
        out.extend(doh('getip.supervpn.cc'))
    except Exception as exc:
        if verbose:
            print('  [warn] doh getip:', exc)
    seen, dedup = set(), []
    for entry in out:
        if entry not in seen:
            seen.add(entry)
            dedup.append(entry)
    return dedup


class SuperVpnAccount:
    def __init__(self, device_id=None, username=None):
        self.device_id = device_id or str(uuid.uuid4())
        self.username = username or ('supro_' + md5(str(uuid.uuid4())))
        self.app_version = '2.0.9'
        self.app_version_code = '209'
        self.user = None
        self.gateways = []
        self.gateway = None

    def _base_params(self):
        return {'platform': 'a', 'channel': 'play', 'alias': 'supro',
                'appVersionCode': self.app_version_code,
                'appVersion': self.app_version,
                'androidVersion': '34', 'localGeo': ''}

    def register(self):
        params = self._base_params()
        params.update({
            'username': self.username,
            'deviceId': self.device_id,
            'imsi': self.device_id,
            'manufacturer': 'Google',
            'model': 'sdk_gphone64_x86_64',
            'display': 'UE1A.230829.036',
            'serial': 'unknown',
            'secret': md5(self.username + self.device_id + '2y6w498L2I'),
            'package': 'com.jrzheng.supervpnpayment',
            'sign': '',
        })
        return self._call('/api/register.json', params)

    def locations(self):
        user = self.user or {}
        params = self._base_params()
        params.update({
            'username': user.get('username', ''),
            'password': user.get('password', ''),
            'signInUsername': user.get('signInUsername', ''),
            'signInPassword': user.get('signInPassword', ''),
            'loginType': str(user.get('loginType', 0)),
            'sign': user.get('sign', ''),
        })
        return self._call('/api/location.json', params)

    def acquire(self, location):
        """location 为 location.json 里的地区码（auto/nl/de/...）"""
        user = self.user or {}
        params = self._base_params()
        params.update({
            'username': user.get('username', ''),
            'password': user.get('password', ''),
            'signInUsername': user.get('signInUsername', ''),
            'signInPassword': user.get('signInPassword', ''),
            'loginType': str(user.get('loginType', 0)),
            'location': location,
            'deviceId': self.device_id,
            'imsi': self.device_id,
            'package': 'com.jrzheng.supervpnpayment',
            'sign': '',
            's1': '', 's2': '',
            'tcp': 'true', 'hy2': 'true', 'tcpOnly': 'false', 'configVersion': '0',
        })
        return self._call('/api/acquire.json', params)

    def _call(self, path, params):
        if not self.gateways:
            self.gateways = fetch_gateways()
        host_list = ([self.gateway] if self.gateway else []) + self.gateways
        errors = []
        for entry in host_list:
            host = entry.replace('https://', '').replace('http://', '').rstrip('/')
            try:
                result = api(host, path, params)
                self.gateway = host
                return result
            except Exception as exc:
                errors.append('  ' + host + ': ' + type(exc).__name__ + ' ' + str(exc)[:100])
        raise RuntimeError('all gateways failed:\n' + '\n'.join(errors))
