
SuperVPN 3.1.9 免费版 - 第一轮压测记录 (2026-11-13)
=====================================================
样本: SuperVPN_v3.1.9_VIP-apk4free.net.apk, md5 2b64ea42abe75d8dae767af270ec1b7b
     与 11-11 HEROMODS 版完全同 md5 -> 同一二进制, 无新静态信息

注册链路 (direct, 不走 10808):
  register.json -> 0, 拿到 free_fa5bc1fa... / pw 09706561
  location.json -> 0, 12 地区
  acquire.json  -> -1 (免费用户正常标记), gateways 有货
  新鲜 103 条 gateway / 103 唯一 ip (11-11 是 94; 两天重叠仅 1 -> 端口池每天轮换)
  gwType 全 0 (IKEv2/IPSec)
  84 条带 psk (全部 88Oqer5Nep), 19 条无 psk (证书认证型)
  cert CN=geckovpn (和 2.0.9 付费版同 CA)
  sessionTime=43200 (12h)

压测结果:
  1) ike-scan --ikev2 x84: 0 handshake 0 notify (全部静默)
  2) 手工 UDP: 无 ICMP 不可达, 静默丢
  3) charon-cmd (宿主→网关直连): IKE_SA_INIT 发出, 3 次重传全无响应
  4) 网关只响应 UDP 500? 客户端端口 45866 -> 网关 4500 无响应
  5) 网关无 DNS 反解 (PTR 失败) -> 非标准主机名

APK 侧还原 (libandroidbridge.so):
  connection.* 键: server/port/username/password/local_id/remote_id/
                   type/ike_proposal/esp_proposal/certreq/strict_revocation
  profile 枚举: ikev2-byod / ikev2-eap / ikev2-cert / ikev2-eap-tls / ikev2-byod-eap / ikev2-cert-eap
  无 ikev2-psk 枚举 -> 客户端把 PSK 塞进 EAP 流程 (eap-identity + eap-mschapv2/md5/gtc)
  插件链: eap-identity eap-mschapv2 eap-md5 eap-gtc eap-tls
  charon 版本 5.9.2 (服务端 5.9.13)

下一步 (第二轮):
  A. charon-cmd --profile ikev2-eap --eap-identity <username> (模拟 byod 流程)
  B. swanctl 完整 profile (ikev2-eap + mschapv2, username/password)
  C. 模拟器内抓包确认客户端真实 proposal/身份

------------------------------------------------------------
补充 (同日 09:50): 后端 API 连通性恶化
- harvest 成功后 ~35 分钟, www.supervpn.best TCP/TLS 从宿主与 WSL 均 connect 超时
- 域名走 Cloudflare (104.21.9.175 / 172.67.161.35), 对这两个边缘 IP 直连 443 也超时
- 对照组 google/baidu 秒通, 排除本机断网
- 判断: 本机公网出口到 supervpn.best 的 CF 边缘链路间歇性劣化/被阻断;
  harvest 时通时不通, 后续脚本必须带重试与备用出口
- 这也让「网关 IKE 静默」多了一种解释: 网关与 API 同源部署时, 链路问题会同时影响两者
