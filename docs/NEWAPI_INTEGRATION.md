# SuperVPN Pro → new-api 代理池对接指南

> 面向 new-api 对接会话。本仓库只负责**还原节点与协议**，不替对接方做线上决策。
> 先读《逆向报告》§7 合规判定：这批节点属运营方自有 VPS，**默认不建议进对外计费链路**。

---

## 1. 一句话链路

```
acquire.json ──> hysteria2 凭证 ──> mihomo 每节点一个 socks5 监听 ──> new-api 代理池
   (已打通)         (user:pass@ts@md5)      (127.0.0.1:45000+i)          (TRYINGOPEN_PROXY_FILE)
```

## 2. 为什么必须经过 mihomo（本地转换层）

节点是 **Hysteria2 / QUIC(UDP)**，new-api 只会说 HTTP/SOCKS5。所以中间必须有一个
「hy2 → SOCKS5」的本地转换层。三条路，按推荐度排序：

| 方案 | 做法 | 评价 |
|---|---|---|
| **A. mihomo 常驻**（推荐） | 一份 config，`proxies` 全部 hysteria2，`listeners` 每节点一个 socks 端口 | 单进程多端口、自带健康探测 API、已验证 |
| B. sing-box | `outbounds` + 多个 `socks` inbound，用 `route.rules.inbound` 绑定 | 可行，配置更啰嗦 |
| C. 官方 hysteria2 CLI | 每节点一个进程 | 65 个进程，资源浪费 |

本仓库已实现 A：`tools/mihomo_pool.py`（`pool_test.py` / `serve_pool.py` / `emu_verify.py` 复用同一套生成逻辑）。

---

## 3. 落地步骤（方案 A）

### 3.1 生成代理池文件

```powershell
# 1) 账号（首次自动注册） -> recon/account.json
python tools\harvest.py

# 2) 收割 + 即时压测 -> recon/final/alive_nodes.json（只要当轮新鲜凭证可用的节点）
python tools\harvest_test.py 2

# 3) 生成 new-api 可直接读的代理文件（每行 socks5://127.0.0.1:PORT）
python tools\emit_proxy_file.py --base-port 45000 --out proxies.txt
```

生成的 `proxies.txt` 每行形如：

```
socks5://127.0.0.1:45000
socks5://127.0.0.1:45001
```

### 3.2 起转换层

```powershell
python tools\mihomo_pool.py        # 生成 recon/pool/config.yaml 并后台常驻 mihomo
```

mihomo 内核（Clash.Meta）需 v1.19.2 及以上才支持 hysteria2。
优先从 <https://github.com/MetaCubeX/mihomo/releases> 下载对应平台版本，
用环境变量 `MIHOMO_BIN` 指定路径，或放进 PATH。

### 3.3 接进 new-api

当前仓库 `relay/channel/tryingopen/proxy_pool.go` 已支持文件注入：

```
TRYINGOPEN_PROXY_FILE=<绝对路径>/proxies.txt
TRYINGOPEN_PROXIES=                # 留空，避免环境变量串味
```

`LoadProxies()` 按行读取，`socks5://host:port` 与 `http://user:pass@host:port` 都吃。
池子自带冷却、健康分、每日限额（见 `proxy_pool.go` 顶部注释），无需额外改造。

### 3.4 刷新节奏

- **凭证**：服务端 `sessionTime = 43200s`（12h），实测 T+300s 仍全通；建议 **6h 刷新一次**
  （留一半余量），即 `harvest_test.py` 定时跑。
- **节点列表**：服务端每轮 acquire 都会轮换，一次只能拿到每地区 1–8 个；
  想要大池就多轮 `harvest_multi.py`，但**只把「当轮新鲜凭证 + 当场压测通过」的节点写进 proxies.txt**。
- 刷新后需要 `ReloadProxyPool`（后台「代理池 → 重新加载」按钮，对应
  `controller/twoapi_panel.go:323 ReloadProxyPool`）或重启 mihomo + 触发重新 LoadProxies。

---

## 4. 已知坑（别再踩）

1. **API 路径必须在加密体里**，`POST https://<gw>/`；放 URL 上会得到 `xxx is empty` 的假报错。
2. **响应有两种**：成功是 `Base64(DES)`，失败是明文 JSON。只按密文解析会把错误吞掉。
3. **网关源三个里只有一个活着**（`kkio778.pages.dev`），另外两个会 RST/超时 —— 必须有 failover。
4. **不要信 `location` 字段**：11/59 个节点标称国家 ≠ 真实出口国，按出口 IP 打标。
5. **凭证与地区绑定**：用 A 地区 acquire 拿到的凭证去连 B 地区的节点会失败；
   一个节点只保留它被 acquire 出来那一轮的凭证。
6. **必须用「acquire 后立刻测」的流水线**：先批量收割再统一压测，会因为节点轮换 + 凭证过期
   把真实可用率从 59/190 严重低估。
7. 本机 `curl` 需要 `--ssl-no-revoke`；GitHub release 下载会被 reset，用镜像或复用既有二进制。
8. 本机 DNS 对 `*.firebaseio.com` 被污染成 `127.0.0.1`，需要 DoH 解析（`httpx_direct.py` 里已做）。

---

## 5. 如果只想「要一批能用的 IP」

- 直接取 `output/sub_hy2_alive.txt`（明文 URI）或 `output/clash_hy2_alive.yaml`，
  丢进任意支持 hysteria2 的客户端（mihomo / NekoBox / Shadowrocket / sing-box）。
- 出口 IP 清单见 `output/nodes_hy2.json` 的 `egress_ip` 字段（59 个，8 国）。
- 订阅链接（本地 demo）：`http://127.0.0.1:8930/sub/hy2/b64`、`/sub/clash`、`/sub/singbox`。

---

## 6. 合规红线（务必转达）

- 这些 IP 是 SuperVPN 运营方租用的生产服务器，凭证"能取到"≠"有使用权"。
- 不得把本仓库产物作为**对外售卖/计费**的代理来源。
- 建议用途：协议研究、连通性探测、自有池的临时补充（且需自行评估风险）。
- 若线上代理池要对客户承诺 SLA，请改用自有 VPS 或 VPNGate 等公开志愿源。
