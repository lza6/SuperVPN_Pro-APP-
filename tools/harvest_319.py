#!/usr/bin/env python3
"""SuperVPN 3.1.9 Free 节点收割: 免密注册 -> location -> acquire.

协议与 2.0.9 付费版同源（明文 form 而非 DES 信封），secret = md5(username+deviceId+SALT)。
"""
import hashlib, json, os, sys, urllib.parse, urllib.request, uuid, ssl

BASE = "https://www.supervpn.best"
SALT = "2y6w498L2I"
PKG = "com.jrzheng.supervpnfree"
ALIAS, PLATFORM, CHANNEL = "free", "a", "play"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))

def call(path, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={
        "User-Agent": "okhttp/4.12.0", "Content-Type": "application/x-www-form-urlencoded"})
    raw = OP.open(req, timeout=20).read().decode("utf-8", "replace").strip()
    return json.loads(raw) if raw.startswith("{") else {"status": -99, "raw": raw[:300]}

def register():
    uid, dev = str(uuid.uuid4()), str(uuid.uuid4())
    user = "free_" + hashlib.md5(uid.encode()).hexdigest()
    p = dict(username=user, deviceId=dev, imsi=dev, package=PKG, platform=PLATFORM,
             channel=CHANNEL, alias=ALIAS, manufacturer="Xiaomi", model="M2012K11AC",
             display="MIUI", serial=dev, appVersionCode="319", appVersion="3.1.9",
             androidVersion="34", sign="", localGeo="",
             secret=hashlib.md5((user + dev + SALT).encode()).hexdigest())
    r = call("/api/register.json", p)
    if r.get("status") != 0: raise RuntimeError("register failed: %s" % r)
    u = r["data"].get("user", r["data"])
    return {"username": u["username"], "password": u["password"], "deviceId": dev,
            "platform": PLATFORM, "channel": CHANNEL, "alias": ALIAS,
            "appVersionCode": "319", "sign": "", "localGeo": ""}

def authed(acct, extra=None):
    p = dict(acct); p.update(extra or {}); return p

def main():
    os.makedirs(OUT, exist_ok=True)
    acc_path = os.path.join(OUT, "account.json")
    if os.path.exists(acc_path) and "--new" not in sys.argv:
        acct = json.load(open(acc_path, encoding="utf-8"))
        print("reuse account:", acct["username"])
    else:
        acct = register()
    print("registered:", acct["username"], "pw:", acct["password"])
    loc = call("/api/location.json", authed(acct))
    print("locations status:", loc.get("status"))
    locs = loc.get("data", {}).get("locations", []) if loc.get("status") == 0 else []
    print("  ", [x.get("location") for x in locs][:20])
    all_nodes = {}
    for L in (locs or [{"location": "auto"}]):
        code = L["location"]
        r = call("/api/acquire.json", authed(acct, {"location": code}))
        data = r.get("data") or {}
        if not data.get("gateways"):
            print(f"  acquire {code}: status={r.get('status')} no gateways"); continue
        all_nodes[code] = data
        print(f"  acquire {code}: status={r.get('status')} gateways={len(data['gateways'])} keys={list(data.keys())}")
    json.dump(all_nodes, open(os.path.join(OUT, "acquire_all.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(acct, open(os.path.join(OUT, "account.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("saved -> data/acquire_all.json")

if __name__ == "__main__":
    main()
