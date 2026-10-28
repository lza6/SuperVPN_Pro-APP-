# SuperVPN Pro 2.0.9 · 逆向与 Hysteria2 代理池

逆向 `com.jrzheng.supervpnpayment` v2.0.9 的自有 API（DES 双向加密信封），还原节点下发链路，
把 **Hysteria2 (QUIC/UDP)** 节点批量收割为本地 SOCKS5 代理池，附完整压测数据与订阅产物。

> ⚠️ **合规声明（必读）**
> 本项目收割到的节点是 SuperVPN 运营方**自己租用的生产服务器**（Zenlayer / OVH / Scaleway / LeaseWeb 等）。
> 凭证"能取到"≠"有使用权"。本项目仅用于**协议研究与连通性验证**，
> **不构成**对第三方基础设施的使用授权，**不可**作为对外的售卖/计费代理来源。
> 详见 [`docs/REVERSE_REPORT.md` §7](docs/REVERSE_REPORT.md)。

---

## 状态

| 环节 | 状态 | 说明 |
|---|---|---|
| 网关发现 | ✅ | 3 源 + DoH 兜底，实测仅 `kkio778.pages.dev` 稳定 |
| DES 信封（请求/响应） | ✅ | `POST /` + `path` 在密文体；成功密文/失败明文双形态 |
| 免密注册 | ✅ | `username`/`deviceId`/`secret` 全可伪造，`sign` 可留空 |
| 节点下发 `acquire.json` | ✅ | 4 类网关：IKEv2 / 私有 TCP / **Hysteria2** / relay |
| Hysteria2 批量收割 | ✅ | 4 轮 × 12 地区 = **190 个端点** |
| 真实存活压测 | ✅ | **59 可用 / 8 国**（当轮新鲜凭证即时压测） |
| 带宽 | ✅ | 12–52 Mbps，中位 ~30 Mbps |
| 并发 | ✅ | 单节点 8 并发 **8/8** |
| 凭证寿命 | ✅ | 服务端 `sessionTime=12h`，实测 T+300s 仍 8/8 |
| **模拟器内实测** | ✅ | **56/59**，出口 IP == 节点 IP（不经宿主 v2ray） |
| 订阅产物（URI/Clash/sing-box） | ✅ | URI 反向解析实测 **5/5** |
| demo Web 服务 | ✅ | 截图见 `screenshots/demo.png` |
| 可入生产代理池 | ❌ | 运营方自有基础设施，不建议（见合规声明） |

---

## 协议速查（三句话）

1. **网关**：`kkio778.pages.dev` 等三源返回网关 IP 列表，DoH `getip.supervpn.cc` 兜底。
2. **信封**：`POST https://<gateway>/`，body = `Base64(DES-ECB(JSON))`，
   key = `Base64("6c"+"GDH"+"63lEyY=")`；**API 路径写在密文的 `path` 字段里**。
3. **节点**：`/api/acquire.json {location: xx}` → `hy2Gateways[].config.@HY2_AUTH`
   = `user:pass@<unix_ts>@<md5>`，SNI 用假域名伪装，模板自带 `socks5.listen`。

👉 完整实现手册：[`docs/PROTOCOL.md`](docs/PROTOCOL.md)
👉 完整逆向报告：[`docs/REVERSE_REPORT.md`](docs/REVERSE_REPORT.md)
👉 new-api 对接：[`docs/NEWAPI_INTEGRATION.md`](docs/NEWAPI_INTEGRATION.md)

---

## 快速开始

```powershell
pip install pycryptodome PySocks PyYAML

# 需要 mihomo(Clash.Meta) v1.19.2+ 作为 hysteria2 -> SOCKS5 转换层
$env:MIHOMO_BIN = "D:\path\to\mihomo.exe"      # 或用 PATH 里的 mihomo

python tools\harvest.py                # 注册（首次自动）+ 单轮 12 地区收割
python tools\harvest_test.py 2         # 收割 + 即时压测  -> recon/final/alive_nodes.json
python tools\serve_pool.py             # 常驻：socks5://127.0.0.1:45000..4505x
python tools\emit_proxy_file.py        # -> proxies.txt（给 new-api 等外部程序）
python tools\subgen.py                 # -> output/ 订阅产物
python tools\demo_server.py            # -> http://127.0.0.1:8930
```

其它工具：

```powershell
python tools\bench.py throughput       # 带宽
python tools\bench.py concurrency      # 并发
python tools\bench.py credential       # 凭证寿命
python tools\emu_verify.py 59          # 模拟器内实测（需 adb + emulator）
python tools\verify_sub.py              # 订阅 URI 反向解析自检
```

---

## 目录

```
docs/
  PROTOCOL.md              协议速查（写给二次开发/对接方）
  REVERSE_REPORT.md        完整逆向报告（架构、踩坑、实测、合规判定）
  NEWAPI_INTEGRATION.md    new-api 代理池对接指南
tools/
  supervpn_client.py       DES 信封 + 注册 + location + acquire（协议核心）
  harvest.py               单轮全地区收割
  harvest_multi.py         多轮收割（服务端节点列表会轮换）
  harvest_test.py          收割 + 即时压测（推荐主流水线）
  mihomo_pool.py           节点 -> 本地 SOCKS5 端口映射 + 全量压测
  serve_pool.py            常驻代理池（给外部程序用）
  pool_test.py             对多轮累积池做全量压测
  retest.py                失败节点用新鲜凭证复测
  bench.py                 带宽 / 并发 / 凭证寿命
  emu_verify.py            模拟器内实测（宿主 TCP->SOCKS5 转发 + adb nc）
  subgen.py                订阅生成（URI / Clash / sing-box）
  verify_sub.py            订阅 URI 反向解析 + 真实连接自检
  emit_proxy_file.py       生成 socks5:// 清单（new-api TRYINGOPEN_PROXY_FILE）
  quic_probe.py            QUIC 存活探测（本机 UDP 常被限，仅供参考）
  livetest.py              早期单节点 hysteria2 客户端测活（需 hysteria.exe）
  httpx_direct.py          DoH 解析 + 直连 IP 的 HTTP 客户端（绕环境代理/DNS 污染）
  demo_server.py           demo 服务 :8930
samples/                   脱敏样例（凭证有效期 12h，需自行 acquire）
screenshots/               demo 页与模拟器截图
```

---

## 实测数据

### 存活率

| 指标 | 值 |
|---|---|
| 投放端点 | 190（4 轮 × 12 地区去重） |
| 当轮新鲜凭证即时压测通过 | **59** |
| 有效国家 | FR 16 / CA 10 / DE 7 / US 6 / SG 6 / PL 6 / NL 6 / GB 2 |
| 出口 IP == 节点 IP | 59/59（全部直出，无二次中转） |
| 时延（CN → 节点，含握手） | 0.79–6.1 s，中位 ~1.5 s |
| 失败原因 | 100% 为 QUIC 层直接断开，无 DNS 类失败 |

**节点集合会轮换**：同日第二次整轮刷新得到 51 个可用（与首轮重叠约 7 成）。
做容量规划按"当轮结果"算，不要写死数字。

### 带宽（10 MB 实测，经节点出口到 speed.cloudflare.com）

```
162.128.84.0    SG   52.0 Mbps      51.79.93.3      CA   30.0 Mbps
162.128.86.15   SG   51.3 Mbps      162.128.71.74   SG   39.0 Mbps
162.128.85.125  SG   50.1 Mbps      23.251.43.201   US   34.0 Mbps
162.128.71.225  SG   48.0 Mbps      107.151.199.73  US   33.2 Mbps
162.128.70.151  SG   50.3 Mbps      78.232.130.5    FR   12.3 Mbps
```

### 模拟器内实测（与宿主 v2ray 完全隔离）

做法：宿主跑 mihomo（每节点一个 `127.0.0.1:<独立端口>`），再起「裸 TCP → SOCKS5」转发器
（只绑 `127.0.0.1`），模拟器通过 `10.0.2.2:<port>` 访问；请求由 `adb shell` 内的 `nc` 发起。

```
模拟器裸出口（未走隧道）: 183.250.230.8 (CN)
OK  107.151.199.73  US  模拟器出口=107.151.199.73
OK  162.128.84.0    SG  模拟器出口=162.128.84.0
OK  158.51.123.139  CA  模拟器出口=158.51.123.139
...
模拟器内真实转发成功 56 / 59
```

全程未修改系统代理、未读写 v2rayN 配置、未占用 10808/10809。

---

## 路线图

- [x] 网关发现 + DES 信封还原
- [x] 免密注册 + location + acquire
- [x] Hysteria2 节点收割与真实压测
- [x] mihomo 转换层 + 常驻 SOCKS5 池
- [x] 订阅产物（URI / Clash / sing-box）+ 自检
- [x] 模拟器内隔离实测
- [ ] 私有 TCP 隧道（gwType 2）协议完整还原
- [ ] relay 中继层行为分析
- [ ] 自动轮换守护进程（凭证到期前自动重收割 + 热重载 mihomo）

---

## 免责声明

本项目为安全研究性质，仅用于学习协议实现与网络连通性验证。
请勿用于任何侵犯第三方服务条款或违反当地法律法规的用途。
由使用者自行承担全部责任。

## License

MIT（见 [LICENSE](LICENSE)）
