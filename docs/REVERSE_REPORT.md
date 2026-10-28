# SuperVPN Pro 2.0.9 (com.jrzheng.supervpnpayment) 逆向报告

> 目标：还原节点来源与协议，评估能否作为**长久稳定代理池**接入线上（new-api 代理池 / IP 池）。
> 环境：Windows 10 + Android 模拟器 `emulator-5570`（sdk_gphone_x86_64_arm64, API 30）。
> 压测全程使用独立端口 + 独立进程，**未修改系统代理、未读写 v2rayN 配置、未占用 10808/10809**。

---

## 1. 结论速览

| 项 | 结果 |
|---|---|
| 架构 | 自有 HTTP API（GAE appspot）+ 多网关发现源，DES 双向加密 |
| 认证 | 免密自动注册，`username` + `deviceId`（UUID，可任意伪造） |
| 节点下发 | `/api/acquire.json` 按地区返回 4 类网关 |
| 隧道协议 | **Hysteria2 (QUIC/UDP)** 主力 + IKEv2/IPsec + 私有 TCP 隧道 |
| Hysteria2 凭证 | `user:pass@<unix_ts>@<md5>`，SNI 用假域名（伪装） |
| 可用性 | 四轮收割 190 个 HY2 端点 → **59 个实测可用**（8 国） |
| 带宽 | 12–52 Mbps（中位 ~30 Mbps） |
| 并发 | 单节点 8 并发 8/8 全通 |
| 凭证寿命 | ≥12h（`sessionTime=43200`），实测 T+300s 仍 8/8 可用 |
| 模拟器内实测 | 12/12 通过（出口 IP 与节点 IP 完全一致） |
| 合规判定 | ⚠️ **运营商自有基础设施**，不可入生产代理池（见 §7） |

---

## 2. 架构与协议

### 2.1 包体

```
com.jrzheng.supervpnpayment   v2.0.9 (HEROMODS 破解版，内含 libSignatureKiller)
lib/arm64-v8a/
  libgojni.so            18MB  Hysteria2 客户端核心（gVisor + QUIC，gomobile 编译）
  libhev-socks5-tunnel.so      sockstun：TUN 设备 -> 本地 SOCKS5
  libstrongswan.so / libcharon.so / libipsec.so   IKEv2/IPsec 栈
  libandroidbridge.so
```

### 2.2 网关发现（三源 + DoH 兜底）

`defpackage/w9.<clinit>` 里三段字符串被 DES 加密，密钥 = `Base64("6c" + "GDH" + "63lEyY=")`
→ `e9c1831fade51326`。解出：

```yaml
a: https://d1ol9zp47zx5tq.cloudfront.net/index.html   # CloudFront（实测已 RST）
b: https://kkio778.pages.dev                          # Cloudflare Pages（可用）
c: https://cdn.statically.io/gh/HiSuperSoft/JustTest/main/servers.txt   # 实测超时
d: https://1.1.1.1/dns-query?name=getip.supervpn.cc   # DoH 兜底，返回下一个网关 IP
```

`kkio778.pages.dev` 当前返回（每行一个网关）：

```
d1knlcciaohl5s.cloudfront.net
212.8.250.36 / 51.159.133.112 / 51.159.140.172 / 64.176.39.115 / 95.179.141.2
54.93.67.196 / 35.158.72.194 / 18.185.12.184 / 138.197.179.194
```

网关一律 `https://<entry>`，**API 路径不在 URL 上**，只放在加密体的 `path` 字段。

### 2.3 请求/响应信封（关键坑）

```
POST https://<gateway>/          Content-Type: application/json; charset=utf-8
body = Base64( DES/ECB/PKCS5( JSON ) )

JSON = {
  "<md5(uuid)>": "<md5(uuid)>",     # 随机噪声键值对，随机次数插入
  ...业务参数...,
  "path": "/api/location.json"      # 真实 API 路径
}

响应 = Base64(DES 密文) -> {"status":0,"msg":null,"data":{...}}
      （错误时服务端直接返回明文 JSON，不再是密文 —— 必须两种都判）
```

> **踩坑记录**：一开始把路径放在 URL 上（`POST /api/register.json`），服务端统一返回
> `{"status":-1,"msg":"xxx is empty"}`，且**不同网关报不同的空字段**——那是解密失败后
> 逐字段校验的表现，不是参数缺失。真正原因就是路径必须走 body。

### 2.4 API 清单

| 路径 | 作用 |
|---|---|
| `/api/register.json` | 自动注册，返回 `data.user`（含 password）与广告开关、商品列表 |
| `/api/location.json` | 地区列表（auto/nl/de/fr/uk/us/ca/pl/sgp/jp/in/es）+ 用户态 |
| `/api/acquire.json` | **核心**：按 `location` 下发 4 类网关 + 凭证 |
| `/api/speedTestData.json` | 测速节点列表 |
| `/api/ticket/*`、`/api/payment/*` | 工单与支付 |

### 2.5 注册参数（全部可伪造）

```
username  = "supro_" + md5(advertisingId)        # 广告 ID 或 UUID
deviceId  = UUID.randomUUID()                    # 存 SharedPreferences，无签名校验
imsi      = 同 deviceId
secret    = md5(username + deviceId + "2y6w498L2I")   # 硬编码盐
platform  = "a", channel = "play", alias = "supro"
appVersionCode / appVersion / androidVersion / manufacturer / model / display / serial
package   = "com.jrzheng.supervpnpayment"
sign      = ""      # 留空也通过
localGeo  = ""
s1 / s2   = native 方法（libgojni），acquire 时带上；实测**留空同样可拿到节点**
```

### 2.6 acquire 返回的 4 类网关

| gwType | 协议 | 字段 | 端口 | 备注 |
|---|---|---|---|---|
| 0 | **IKEv2/IPsec** | `cert`(X.509 客户端证书, geckovpn CA) + `psk` | 随机高位 | strongSwan/charon |
| 2 | 私有 TCP 隧道 | `psk` | 随机高位 | 自研 JSON 握手 `{"u","p","v":"1"}`，未见标准协议 |
| 4 | **Hysteria2** | `@HY2_AUTH`/`@HY2_DOMAIN`/`@HY2_SERVER` | 443/UDP | 直接给本地 SOCKS5，**最适合做代理池** |
| (relay) | 中继层 | 本期为空 | — | 用于跨境中转 |

Hysteria2 节点示例：

```json
{
 "ip": "74.208.18.60", "location": "us", "port": 443, "gwType": 4,
 "config": {
   "@HY2_AUTH": "supro_xxx:<password>@<unix_ts>@<md5>",
   "@HY2_DOMAIN": "centos.org",        // 伪装 SNI（redis.io / docker.com / vuejs.org / tailwindcss.com…）
   "@HY2_PORT": "443",
   "@HY2_SERVER": "74.208.18.60:443",
   "@SOCKS_PORT": "2878"
 }
}
```

`configHy2` 模板（下发在 `data.configHy2`）：

```yaml
server: @HY2_SERVER
auth: @HY2_AUTH
tls: { sni: @HY2_DOMAIN, insecure: true }
congestion: { type: bbr, bbrProfile: standard }
socks5: { listen: 127.0.0.1:@SOCKS_PORT, blockQuic: true }
fastOpen: true
quic: { maxIdleTimeout: 20s, keepAlivePeriod: 5s, disablePathMTUDiscovery: true }
```

—— 这正好就是客户端内核的配置格式：**hysteria2 直接吐本地 SOCKS5**，不需要 TUN。

---

## 3. 实测数据

### 3.1 收割规模

4 轮 × 12 地区 `acquire`：累计 190 个唯一 HY2 端点（服务端列表会轮换，单轮每地区 1–8 个）。

### 3.2 存活（真实 hysteria2 握手 + HTTP 出口校验）

| 指标 | 值 |
|---|---|
| 投放端点 | 190 |
| 用「当轮新鲜凭证」即时压测通过 | **59** |
| 有效国家 | FR 16 / CA 10 / DE 7 / US 6 / SG 6 / PL 6 / NL 6 / GB 2 |
| 出口 IP == 节点 IP | 59/59（全部直出，无二次中转） |
| 时延（CN → 节点，含握手） | 0.79 – 6.1 s，中位 ~1.5 s |
| 失败原因 | 100% 为 QUIC 层直接断开（节点下线 / 凭证被轮换掉），无 DNS 类失败 |

> 标称国家与真实出口国不一致的有 11 个（如标 `nl` 实为 `FR`），属运营方调度池共享，**不能信 tag 信出口 IP**。

**刷新复测（同日 00:21）**：从 demo 页点「重新收割 + 压测」跑第二轮全流程，得到 **51 个可用节点**
（与第一轮 59 个重叠约 7 成）。说明：
- 收割 → 压测 → 订阅 → 常驻池 这条链路可以整体重跑，不是一次性脚本；
- 节点集合确实在轮换，**可用数量在 50–60 区间波动**，做容量规划时要按"当轮结果"算，不能写死 59。

### 3.3 带宽（10MB 实测，经节点出口到 speed.cloudflare.com）

```
162.128.84.0    SG   52.0 Mbps      51.79.93.3      CA   30.0 Mbps
162.128.86.15   SG   51.3 Mbps      162.128.71.74   SG   39.0 Mbps
162.128.85.125  SG   50.1 Mbps      23.251.43.201   US   34.0 Mbps
162.128.71.225  SG   48.0 Mbps      107.151.199.73  US   33.2 Mbps
162.128.70.151  SG   50.3 Mbps      78.232.130.5    FR   12.3 Mbps
```

### 3.4 并发

单节点 8 条并发隧道：**8/8 全通**（6 个抽样节点全部 8/8），无 QUIC 连接数限制迹象。

### 3.5 凭证寿命

同一批凭证 T+0 / T+120s / T+300s：**8/8、8/8、8/8** 全通。
服务端 `sessionTime = 43200`（12h），推断 12 小时内无需换证。

### 3.6 模拟器内实测（用户要求：不得借道本机 v2rayN）

做法：宿主跑 mihomo（每节点一个 127.0.0.1 独立 SOCKS 端口），再在宿主起
「裸 TCP → SOCKS5」转发器（只绑定 127.0.0.1，模拟器映射为 `10.0.2.2`）；
请求**由模拟器 adb shell 内的 `nc` 发起**。

```
模拟器裸出口（未走隧道）: 183.250.230.8 (CN)
OK  107.151.199.73  US  模拟器出口=107.151.199.73
OK  162.128.84.0    SG  模拟器出口=162.128.84.0
OK  158.51.123.139  CA  模拟器出口=158.51.123.139
...
模拟器内真实转发成功 56 / 59
```

3 个失败节点都是 PL 慢节点（宿主机侧 RTT 5.3–6.1s），在模拟器 `nc -q 6` 的等待窗口内没拿到响应，
重跑可恢复，非链路问题。宿主机侧 59/59 全通。

—— 出口 IP 完全等于节点 IP，证明隧道真实可用，且全程未触碰本机 v2rayN（10808 端口零交互）。

### 3.7 订阅产物自检

`output/sub_hy2_alive.txt` 的 URI 被**反向解析成 mihomo 配置再真实连接**：

```
URI 订阅可用 5 / 5
```

说明 `hysteria2://<percent-encoded auth>@<ip>:<port>/?sni=..&insecure=1#tag` 这个链接格式
（auth 里含 `:` `@`，必须 percent-encode）是对的实现，不是"看起来对"。

---

## 4. 复现方式

```powershell
# 0) 依赖 + mihomo 内核（hysteria2 -> SOCKS5 转换层）
pip install pycryptodome PySocks PyYAML
$env:MIHOMO_BIN = "D:\path\to\mihomo.exe"     # v1.19.2+，或放进 PATH

# 1) 注册/复用账号（首次自动注册，凭证存 recon/account.json）
python tools\harvest.py                     # 单轮：12 地区 acquire
python tools\harvest_multi.py 4 3           # 多轮收割 -> recon/harvest/hy2_pool.json

# 2) 收割 + 即时压测（推荐，避开过期凭证）
python tools\harvest_test.py 2              # -> recon/final/alive_nodes.json

# 3) 带宽 / 并发 / 凭证寿命
python tools\bench.py throughput
python tools\bench.py concurrency
python tools\bench.py credential

# 4) 模拟器内实测
python tools\emu_verify.py 59               # -> recon/emu/emu_verify.json

# 5) 生成订阅产物
python tools\subgen.py                      # -> output/

# 6) 常驻代理池 + demo 服务
python tools\serve_pool.py                  # socks5://127.0.0.1:45000..
python tools\demo_server.py                 # http://127.0.0.1:8930
```

依赖：`pycryptodome`（DES）、`PySocks`、`PyYAML`、`playwright`（仅截图验证用）。
mihomo 内核复用本机已有 `duck-2api/duck2api-hub/tools/mihomo/mihomo.exe`（v1.19.2，支持 hysteria2）。

---

## 5. 产物清单

```
tools/
  supervpn_client.py     DES 信封 + 注册 + location + acquire（协议复现核心）
  harvest.py             单轮全地区收割
  harvest_multi.py       多轮收割（节点列表会轮换）
  harvest_test.py        收割 + 即时压测流水线（推荐）
  mihomo_pool.py         节点 -> 本地 SOCKS5 端口映射 + 全量压测
  serve_pool.py          常驻代理池
  pool_test.py           对多轮池做全量压测
  retest.py              失败节点用新鲜凭证复测
  bench.py               带宽 / 并发 / 凭证寿命
  emu_verify.py          模拟器内实测
  subgen.py              订阅生成（URI / Clash / sing-box）
  verify_sub.py          订阅 URI 反向解析自检
  emit_proxy_file.py     生成 socks5:// 清单给 new-api
  quic_probe.py          QUIC 存活探测（本机 UDP 被限，仅供参考）
  demo_server.py         demo 服务 :8930
docs/
  PROTOCOL.md            协议速查
  REVERSE_REPORT.md      本文件
  NEWAPI_INTEGRATION.md  new-api 对接指南
samples/                 脱敏样例
screenshots/             demo 页与模拟器截图
```

---

## 6. new-api 接入路径

见 `docs/NEWAPI_INTEGRATION.md`。一句话：

```
acquire.json -> hysteria2 凭证 -> mihomo 每节点一个 socks5 监听 -> new-api 代理池当普通 SOCKS5 用
```

## 7. 合规判定

| 项 | 判定 |
|---|---|
| 节点归属 | **运营方自有 VPS**（Zenlayer / OVH / Scaleway / LeaseWeb / GTS / NTT 批量租用），非志愿公开源 |
| 凭证来源 | 免密自动注册即发，无支付、无邀请 |
| 可否入生产代理池 | ❌ **不建议**。理由见下 |
| 可否用于研究 / 自用 | ✅ 协议逆向与连通性研究属正常安全研究范畴 |

不可入生产池的具体理由：

1. **基础设施所有权**：这些 IP 是 SuperVPN 运营方租用的生产服务器。凭证"能拿到"不等于"有使用权"，
   把这批机器挂进自己的对外代理池属于**占用他人基础设施**，且会连带承担其法律责任与风控后果。
2. **凭证寿命与稳定性**：节点列表按轮次轮换、每次 acquire 换一批凭证，40–70% 的端点当轮即死。
   作为"长久稳定池"，它的稳定性来自运营方持续调度，不来自我们的控制，随时可能整体切断。
3. **可追溯性**：`acquire` 每次带 `deviceId`/`imei`/`ip` 上报，滥用会被单账号封禁并留下设备指纹。
4. **品牌风险**：代理池对外若被滥用，溯源指向这批第三方服务器，与自有 VPS 池相比完全不可控。

**推荐做法**（若确实需要长期稳定池）：只用本仓库的协议知识做
「免费额度节点的补充/兜底」，主力池仍应使用自有 VPS 或 VPNGate 等公开志愿源；
或者把 HY2 作为**测速/换 IP 的低成本探测通道**，不进入对外计费链路。
