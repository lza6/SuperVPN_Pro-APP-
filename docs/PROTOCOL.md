# SuperVPN Pro 协议速查（v2.0.9）

> 给对接方/二次开发用的最小实现手册。完整分析见 `REVERSE_REPORT.md`。
> 实现参考：`tools/supervpn_client.py`（约 250 行，无第三方依赖除 `pycryptodome`）。

---

## 0. 常量

```python
DES_KEY = base64.b64decode("6c" + "GDH" + "63lEyY=")   # -> e9c1831fade51326 (8 字节)
PACKAGE = "com.jrzheng.supervpnpayment"
CHANNEL = "play"
ALIAS   = "supro"
SECRET_SALT = "2y6w498L2I"
```

---

## 1. 网关发现

三个内置源（`w9.<clinit>` 里被 DES 加密的常量），加一个 DoH 兜底：

```
GET https://kkio778.pages.dev                                      # 实测唯一稳定可用
GET https://d1ol9zp47zx5tq.cloudfront.net/index.html                # 实测 RST
GET https://cdn.statically.io/gh/HiSuperSoft/JustTest/main/servers.txt   # 实测超时
GET https://1.1.1.1/dns-query?name=getip.supervpn.cc                # DoH，返回下一个网关 IP
```

响应是**每行一个网关**（域名或裸 IP），客户端按行 split 后逐个 failover。
裸 IP 要补 `https://` 前缀。

实测样例：

```
d1knlcciaohl5s.cloudfront.net
212.8.250.36
51.159.133.112
64.176.39.115
95.179.141.2
35.158.72.194
138.197.179.194
```

---

## 2. 请求信封

```
POST https://<gateway>/                       ← 注意是根路径，不带 /api/
Content-Type: application/json; charset=utf-8
body = base64( DES-ECB-PKCS5( json.dumps(payload) ) )
```

payload 结构：

```json
{
  "<md5(uuid4)>": "<md5(uuid4)>",
  "...业务参数...": "...",
  "path": "/api/acquire.json"
}
```

- 噪声键值对是**随机次数**插入（原实现按 `random.nextInt(2)` 反复塞），目的是打乱 JSON 结构；
  实测塞 1 次或不塞都能通过，但建议至少塞 1 次保持形状一致。
- **`path` 是唯一的路径载体**。路径写在 URL 上 → 服务端拿不到参数 → 返回形如
  `{"status":-1,"msg":"platform:platform is empty"}` 的假错误（不同网关报的字段名还不一样）。

---

## 3. 响应信封

| 情况 | 形态 |
|---|---|
| 业务成功 | **Base64 密文**，DES 解出 `{"status":0,"msg":null,"data":{...}}` |
| 业务/参数错误 | **明文 JSON**，如 `{"status":-1,"msg":"location:may not be empty"}` |

实现必须两头都判：

```python
text = payload.decode().strip()
result = json.loads(text) if text.startswith('{') else json.loads(des_decrypt(text))
```

`status == 0` 才算成功；`-11` = VIP 过期，`-15` = 需要购买。

---

## 4. 注册 `/api/register.json`

| 参数 | 值 |
|---|---|
| `username` | `"supro_" + md5(advertisingId)`，advertisingId 可用任意 UUID 代替 |
| `deviceId` | `uuid4()`（原本存 SharedPreferences，无签名校验） |
| `imsi` | 同 `deviceId` |
| `secret` | `md5(username + deviceId + "2y6w498L2I")` |
| `package` | `com.jrzheng.supervpnpayment` |
| `platform` / `channel` / `alias` | `a` / `play` / `supro` |
| `manufacturer` `model` `display` `serial` | 任意合理值 |
| `appVersionCode` / `appVersion` / `androidVersion` | `209` / `2.0.9` / `34`（实测不严格校验） |
| `sign` | **留空可过**（原实现是 `md5(signature.toCharsString())`） |
| `localGeo` | 留空 |

响应 `data.user`：

```json
{"username":"supro_xxx","password":"<8位随机数字>","channel":"play","alias":"supro",
 "loginType":0,"vipType":0,"vipRemainSec":0,"vipEndSec":0,
 "sessionTime":43200,"dataTimeSec":1793199518}
```

`sessionTime = 43200` 秒（12h）是凭证有效窗口。免密 → 直接拿到 `password`。

---

## 5. 地区列表 `/api/location.json`

参数：`username` `password` `signInUsername` `signInPassword` `loginType`
`platform` `channel` `alias` `appVersionCode` `sign` `localGeo`
（`signIn*` / `sign` 留空可用）

响应 `data.locations`：

```json
[{"location":"auto","country":"auto","name":"Auto select"},
 {"location":"nl","country":"nl","name":"Netherlands"}, ...]
```

实测地区：`auto nl de fr uk us ca pl sgp jp in es`

---

## 6. 节点下发 `/api/acquire.json` ★核心

参数（在 location 基础上追加）：

| 参数 | 说明 |
|---|---|
| `location` | 地区码，**必填**，缺失返回 `location:may not be empty` |
| `deviceId` / `imsi` / `package` | 同注册 |
| `s1` / `s2` | 客户端 native 签名。**实测留空也能拿到节点** |
| `tcp` / `hy2` | `"true"`，声明支持的协议族 |
| `tcpOnly` | `"false"` |
| `configVersion` | `"0"`，服务端回传最新版本号 |

响应 `data` 关键字段：

```
geo / userIp          客户端出口地区与 IP（服务端自己判的，用于调度）
configVersion         配置版本号（首次 11）
configHy2 / configTun 协议模板（含 @占位符）
gateways[]            gwType 0  = IKEv2/IPsec
relayGateways[]       中继层（本期为空）
tcpGateways[]         gwType 2  = 私有 TCP 隧道
hy2Gateways[]         gwType 4  = Hysteria2
```

节点对象：

```json
{
  "ip": "74.208.18.60", "location": "us", "port": 443, "gwType": 4, "order": 5,
  "cert": "none | -----BEGIN CERTIFICATE-----…",
  "psk": "88Oqer5Nep",
  "config": { "@HY2_AUTH": "...", "@HY2_DOMAIN": "...", "@HY2_SERVER": "..." }
}
```

### 6.1 Hysteria2（gwType 4）—— 唯一适合做代理池的一类

```json
"config": {
  "@HY2_AUTH":   "supro_xxx:<password>@<unix_ts>@<md5>",
  "@HY2_DOMAIN": "centos.org",
  "@HY2_PORT":   "443",
  "@SOCKS_PORT": "2878",
  "@HY2_SERVER": "74.208.18.60:443",
  "@HY2_IP":     "74.208.18.60"
}
```

`@HY2_AUTH` 结构：`<username>:<password>@<unix_ts>@<md5>`

- `unix_ts` ≈ 请求时刻 + 4s
- `md5` 同一轮 acquire 内固定，跨轮变化 → **凭证与地区绑定**，A 地区凭证连 B 地区节点必失败

`configHy2` 模板（直接就是客户端配置）：

```yaml
server: @HY2_SERVER
auth: @HY2_AUTH
tls: { sni: @HY2_DOMAIN, insecure: true }
congestion: { type: bbr, bbrProfile: standard }
socks5: { listen: 127.0.0.1:@SOCKS_PORT, blockQuic: true, MaxDiscardPeriod: 15s }
fastOpen: true
quic: { maxIdleTimeout: 20s, keepAlivePeriod: 5s, disablePathMTUDiscovery: true }
```

**关键**：Hysteria2 自带 `socks5.listen`，所以拿到凭证后不需要 TUN、不需要 root，
直接起一个 hysteria2 客户端就能得到本地 SOCKS5 —— 这是它能做代理池的根本原因。

SNI 是伪装域名，实测见过：`centos.org` `redis.io` `docker.com` `vuejs.org`
`tailwindcss.com` `electronjs.org` `debian.org` `curl.se` `w3schools.com`
`visualstudio.com` `fedoraproject.org` —— `tls.insecure: true`，不校验。

### 6.2 IKEv2/IPsec（gwType 0）

- `cert` 是完整 X.509 客户端证书（1159 字节，CA = `geckovpn`，2019–2029）
- 用户名/密码用注册拿到的 `username`/`password`
- `psk` 是 IPsec PSK
- 走 strongSwan/charon，非 root 场景基本只能靠 `VpnService` 实现，**不适合做代理池**

### 6.3 私有 TCP 隧道（gwType 2）

非标准协议：连上后发 JSON `{"u":"<username>","p":"<password>","v":"1"}`，
服务端回 `{"ip":..., "route":...}` 再走自定义帧。未完整还原，优先级最低。

---

## 7. 其它端点

| 路径 | 作用 |
|---|---|
| `/api/speedTestData.json` | 测速节点列表（`data.speedTest[]`） |
| `/api/speedStat.json` | 上报测速结果 |
| `/api/connectionLog.json` | 连接日志上报 |
| `/api/faq.json` / `/api/privacy.json` | 内容页 |
| `/api/ticket/*` | 工单 |
| `/api/payment/*` | 内购 |

---

## 8. Python 最小实现骨架

```python
import base64, hashlib, json, uuid, random
from Crypto.Cipher import DES

DES_KEY = base64.b64decode("6cGDH63lEyY=")

def md5(s): return hashlib.md5(s.encode()).hexdigest()

def des_encrypt(plain: str) -> str:
    pad = 8 - len(plain.encode()) % 8
    data = plain.encode() + bytes([pad]) * pad
    return base64.b64encode(DES.new(DES_KEY, DES.MODE_ECB).encrypt(data)).decode()

def des_decrypt(b64: str) -> str:
    raw = DES.new(DES_KEY, DES.MODE_ECB).decrypt(base64.b64decode(b64))
    pad = raw[-1]
    return raw[:-pad].decode() if 0 < pad <= 8 else raw.decode()

def build_body(path, params):
    j = {}
    nk, nv = md5(str(uuid.uuid4())), md5(str(uuid.uuid4()))
    j[nk] = nv
    for k, v in params.items():
        j[k] = v
        if random.getrandbits(1) == 0:
            j[nk] = nv
    j["path"] = path
    j[nk] = nv
    return des_encrypt(json.dumps(j, separators=(',', ':')))

# POST https://<gateway>/  body=build_body("/api/acquire.json", params)
```

---

## 9. 必须避开的 5 个坑

1. 路径放 URL → 假报错 `xxx is empty`（且不同网关报不同字段）。
2. 只按密文解析响应 → 错误响应是明文 JSON，会被当解密失败吞掉。
3. 用 A 地区凭证连 B 地区节点 → QUIC 直接断开。
4. 先批量收割再统一压测 → 节点已轮换/凭证已过期，可用率被严重低估
   （实测：190 端点事后压测 87 可用 vs. 当轮即时压测 59/59 全通）。
5. 信 `location` 字段判国家 → 11/59 个节点标称国家 ≠ 真实出口国，**一律按出口 IP 打标**。
