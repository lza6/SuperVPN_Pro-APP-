# SuperVPN 3.1.9 Free 版（`com.jrzheng.supervpnfree`）

> 姊妹篇：付费线 `com.jrzheng.supervpnpayment` 2.0.9 见 `PROTOCOL.md` / `REVERSE_REPORT.md`。
> 本文只记 3.1.9 免费版与付费版的差异，以及一条**可复现的节点收割链路**。

---

## 1. 一句话结论

**协议同源，只是后端从 DES 信封换成了明文 form，salt 没换。**

```
POST https://www.supervpn.best/api/register.json      (form-urlencoded)
secret = md5(username + deviceId + "2y6w498L2I")      ← 与 2.0.9 付费版同一个 salt
→ {"status":0,"data":{"user":{"username":...,"password":...}}}

GET/POST /api/location.json  → 12 个地区
GET/POST /api/acquire.json   → gateways[] （每个含 ip/port/psk/cert/gwType）
```

## 2. 与 2.0.9 付费版的差异

| 维度 | 2.0.9 付费（`supervpnpayment`） | 3.1.9 免费（`supervpnfree`） |
|---|---|---|
| 传输 | `POST /` 根路径 + `base64(DES-ECB(JSON))`，路径写在密文 `path` 字段 | 明文 form 直接打 `/api/xxx.json` |
| `alias` | `supro` | `free` |
| `package` | `com.jrzheng.supervpnpayment` | `com.jrzheng.supervpnfree` |
| `secret` | `md5(username + deviceId + SALT)` | **完全相同** |
| `SALT` | `2y6w498L2I` | **完全相同** |
| 节点协议 | Hysteria2（QUIC）+ IKEv2 + 私有 TCP | **全部 `gwType=0`（IKEv2/IPSec）** |
| 返回码 | `0` 成功 | `acquire` 恒返回 `-1`，但 `data.gateways` 有内容（免费用户标记，**不影响取节点**） |

## 3. 收割结果（2026-11-11 快照）

```
注册 1 个账号 → 12 个地区 → 96 条 gateway → 94 个唯一 IP
nl 15 · fr 12 · uk 11 · pl 10 · de 10 · us 10 · ca 10 · sgp 10 · jp 3 · in 2 · es 1
```

每条 `gateways[]` 结构：

```json
{
  "ip": "151.115.51.171",
  "location": "pl",
  "port": 3233,
  "psk": "88Oqer5Nep",
  "gwType": 0,
  "order": 3,
  "config": {},
  "cert": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n"
}
```

- `port` 是**随机高位端口**（3000–18000），不是标准 500/4500
- `psk` 是 IKEv2 预共享密钥（10 字符）
- `cert` 是服务端 X.509 证书（自签，CA CN 形如 `tZOSPY4Y` / `gecko...`）

## 4. 复现

```bash
python tools/harvest_319.py          # 复用 data/account.json
python tools/harvest_319.py --new    # 强制新注册
```

产物：`data/acquire_all.json`（12 地区原始响应）、`data/gateways_full.json`（94 条去重含证书）、`data/gateways.json`（去掉证书的精简版）。

## 5. 为什么还不能直接入池

**IKEv2 不是 SOCKS5。** 要走 new-api，链路是：

```
new-api → socks5://127.0.0.1:P → tun2socks → tun → strongswan charon → 网关:自定义端口
```

三个卡点：

1. **Windows 内置 IKEv2 客户端不支持自定义端口**（锁死 500/4500），这批节点全在 3000–18000，必须用 strongSwan 客户端。
2. **strongSwan Windows 版**需要单独装（<https://www.strongswan.org/download.html>），Linux/Docker 走 `charon` + `swanctl`。
3. **测活未完成**：裸 UDP 探测（含最小 IKE_SA_INIT）对 94 个节点全部超时，符合 IPSec 对格式/来源做静默丢弃的正常行为，**不能据此判死**。真实存活必须建 IKE SA。

## 6. 与 2.0.9 池的关系

2.0.9 那 190 个 Hysteria2 端点走 QUIC，mihomo 直接支持，链路短得多（见 `NEWAPI_INTEGRATION.md`）。
3.1.9 免费版只发 IPSec，**同等条件下优先用 2.0.9 的 hy2 池**；3.1.9 的价值在"免费账号可无限续"——`sessionTime=43200` 秒到期后重跑 `harvest_319.py --new` 即可。

## 7. 加固情况（为什么静态读不动）

- `assets/protected_by_np/ApkDex2c_0ee4089aef0e48ebb80790bb795e9ee6.txt` —— NP 管理器 dex2c
- `lib/arm64-v8a/libstub.so`（455 KB，2026-11-11 00:48 生成）—— 方法体编译产物
- `lib/arm64-v8a/libSignatureKiller.so` —— 签名校验绕过（Mod 作者加的）
- `dex` 里 `com.supersoft.supervpnfree.*` 的 `class_defs` 全部缺失，只有字符串池残留

**所以本文的协议不是逆出来的，是拿 2.0.9 的协议直接打通的**（salt 复用是运气，也是同一家厂商的工程惯性）。


---

## 8. 2026-11-13 复测（apk4free VIP 样本 = HEROMODS 同 md5）

- 新鲜 103 条网关（84 PSK / 19 cert），两日重叠仅 1 → **端口池每日全量轮换**
- IKE 全协议探测（ike-scan v1/v2/aggressive + charon-cmd + swanctl eap-mschapv2）0 响应
- 网关 TCP 全关、无 PTR、ICMP 丢
- APK 客户端 profile 枚举无 `ikev2-psk`，实际走 `ikev2-byod-eap`（eap-identity + mschapv2/md5/gtc）
- **结论**：本机直连建不了 IKEv2 SA；要出可用代理仍走 2.0.9 hy2 线。
  3.1.9 的价值 = 免费账号无限续 + 协议研究样本。
