# SuperVPN 3.1.9 Free → new-api 对接指南（交付给对接会话）

> 写给负责 new-api 对接的会话/同事。本文只讲**怎么接**，协议细节见
> `docs/V319_FREE_EDITION.md` 与 `docs/ROUND1_LOADTEST_20261113.md`。
> 数据口径截至 2026-11-13 第一轮压测。

---

## 0. 先说结论（决定对接方式）

| 事实 | 影响 |
|---|---|
| 3.1.9 免费线下发的 **103 个节点全部是 IKEv2/IPSec**（gwType=0），随机高位端口 + PSK + 自签证书 | new-api 只吃 `socks5/http` 代理 URL，中间必须有 **IKEv2 → SOCKS5** 转换层 |
| **本机（无模拟器）IKE SA 建不起来**：ike-scan v1/v2/aggressive、charon-cmd、swanctl(eap-mschapv2) 全部 0 响应，网关 TCP 全关 | 当前没有可用 socks5 出口可写进池文件；**不要把 3.1.9 节点写进线上池** |
| APK 客户端用 strongSwan 5.9.2 的 `ikev2-byod-eap` 系 profile（eap-identity + mschapv2/md5/gtc），不是标准 psk profile | 复现客户端行为要按 byod-eap 配，PSK(`88Oqer5Nep`) 单独用不行 |
| 节点端口池**每日全量轮换**（11-11 与 11-13 重叠仅 1/103） | 池文件必须每日重收割重生成，不能一次配置长期用 |
| 注册链路 100% 可复现：`register.json` 免密，`secret=md5(user+deviceId+2y6w498L2I)` | 收割脚本 `tools/harvest_319.py` 随时可重跑 |
| `supervpn.best` 走 Cloudflare，本机出口对其边缘 IP 间歇超时 | 收割脚本要带重试；必要时换出口 |
| **2.0.9 付费线（本仓库主线）有 59 个已验证 Hysteria2 出口** | **当前真正能接 new-api 的是 2.0.9 线，不是 3.1.9 线** |

**给对接会话的最短路径：接 2.0.9 的 hy2 池（§1），3.1.9 线等模拟器实测打通后再接（§3）。**

---

## 1. 立即可用：接 2.0.9 Hysteria2 池（推荐）

### 1.1 生成池文件

```powershell
cd <本仓库>
python tools/harvest.py            # 注册/复用账号 -> recon/account.json
python tools/harvest_test.py 2     # 收割 + 当轮压测 -> recon/final/alive_nodes.json
python tools/emit_proxy_file.py --base-port 45000 --out proxies.txt
```

`proxies.txt` 每行一个 `socks5://127.0.0.1:<port>`，只含**当轮压测存活**节点。

### 1.2 起转换层（hy2 → socks5）

```powershell
python tools/mihomo_pool.py        # 生成 config + 常驻 mihomo（127.0.0.1:45000+i）
```

mihomo ≥ v1.19.2（需支持 hysteria2）。用 `MIHOMO_BIN` 指定内核路径。

### 1.3 配 new-api

```
TRYINGOPEN_PROXY_FILE=<绝对路径>/proxies.txt
TRYINGOPEN_PROXIES=
```

- new-api 侧 `LoadProxies()` 逐行读，`socks5://` 与 `http://` 均可；
- 池文件**行尾不要写注释**、**不要用 `vless://` 等未支持 scheme**；
- Docker 部署时注意 `127.0.0.1` 指容器自身——用 `--network host`，或把
  mihomo 的 `listen` 改 `0.0.0.0` 并在池文件里写宿主机可达地址
  （Linux docker0 网关一般是 `172.17.0.1`；Docker Desktop 用 `host.docker.internal`）。

### 1.4 验证清单（对接会话逐条执行）

```bash
# ① 转换层端口活着
netstat -ano | findstr 45000 | findstr LISTENING

# ② 每个入口真的能出网（出口 IP == 节点 IP，不是握手成功就算）
python tools/socks_probe.py        # 或 MultiVPN 仓库的同类脚本

# ③ 池文件格式合法
python -c "import urllib.parse,sys; [urllib.parse.urlparse(l.strip()) for l in open('proxies.txt',encoding='utf-8') if l.strip()]; print('OK')"

# ④ new-api 管理端能看到代理池卡片，计数与池文件行数一致
# ⑤ 刷新后调用 ReloadProxyPool（或重启 new-api）确认热更新生效
```

### 1.5 刷新节奏

- 凭证 `sessionTime=43200s`（12h）→ **6 小时刷一次**（`harvest_test.py` 定时跑）
- 每轮 acquire 节点会轮换 → 只写「当轮新鲜凭证 + 当场测活通过」的节点
- 刷新后整体**覆写** `proxies.txt`，不追加

---

## 2. 池文件与 new-api 参数速查

| 项 | 值 |
|---|---|
| 环境变量 | `TRYINGOPEN_PROXY_FILE`（文件路径，逗号分隔可多个） |
| 支持的 URL scheme | `socks5://` `socks5h://` `http://` `https://` |
| 推荐前缀 | 境外节点用 `socks5h://`（远端解析 DNS，防本地污染） |
| 注释 | 只支持**整行** `#` 注释，行尾注释会破坏 parse |
| 一个文件 | 对应管理端一张「来源卡片」（文件名即卡片名） |
| 热更新 | 管理端「代理池 → 重新加载」= `ReloadProxyPool` |
| 健康机制 | new-api 池自带冷却/健康分/每日限额，无需改造 |

---

## 3. 3.1.9 线：等打通后再接（当前不可用）

若后续在**模拟器**里实测打通了 IKEv2（App 能连上说明网关只认特定来源/参数）：

1. 从模拟器抓包拿到真实 proposal / 身份 / 端口行为；
2. 用 swanctl 按抓到的参数在 WSL/Linux 复现 SA；
3. SA 建立后 `tun0 → tun2socks → 127.0.0.1:<port>` 变 socks5；
4. 池文件写 `socks5h://127.0.0.1:<port>`，走 §1.3 同一套 new-api 配置；
5. 因为端口池每日轮换，刷新脚本要整链路自动化（注册→收割→建 SA→测活→写池→reload）。

**在那之前，不要把 3.1.9 的 IP:端口写进 new-api 池文件**——它们不是 socks/http 代理，
写了也只会产生全失败卡片。

---

## 4. 合规红线（转达对接会话）

- 节点属 SuperVPN 运营方租用的生产服务器，凭证能取到 ≠ 有使用权；
- 不得作为对外售卖/计费的代理来源；
- 线上池要 SLA 请用自有 VPS 或 VPNGate 等公开志愿源。
