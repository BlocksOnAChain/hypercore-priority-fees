#!/usr/bin/env python3
"""Pull L2 book (spread/depth) + 1m realized vol + mark/oracle for a set of
representative markets to parameterize the maker last-look model."""
import json, urllib.request, time, math

URL = "https://api.hyperliquid.xyz/info"
def post(p, t=25):
    req = urllib.request.Request(URL, data=json.dumps(p).encode(),
        headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.loads(r.read().decode())
def fnum(x):
    try: return float(x)
    except: return None

now_ms = int(time.time()*1000)
print("UTC now:", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))

# representative set: dislocated Asian RWA, liquid RWA index, commodity, crypto
SET = [("xyz:SKHX","xyz"),("xyz:SMSN","xyz"),("xyz:SP500","xyz"),
       ("xyz:GOLD","xyz"),("xyz:NVDA","xyz"),("BTC",""),("SOL","")]

# get ctxs for mark/oracle per dex
ctx_by_coin = {}
for dex in ["","xyz"]:
    payload={"type":"metaAndAssetCtxs"}
    if dex: payload["dex"]=dex
    meta,ctxs=post(payload)
    for u,c in zip(meta["universe"],ctxs):
        ctx_by_coin[u["name"]]=c

print(f"\n{'coin':<14}{'spread(bps)':>12}{'bidDepth10bp($k)':>18}{'askDepth10bp($k)':>18}"
      f"{'rv1m(bps)':>11}{'disloc(bps)':>12}{'dayMove%':>10}")
for coin,dex in SET:
    try:
        book=post({"type":"l2Book","coin":coin})
        levels=book.get("levels",[[],[]])
        bids,asks=levels[0],levels[1]
        if not bids or not asks:
            print(f"{coin:<14}  [empty book]"); continue
        b0=fnum(bids[0]["px"]); a0=fnum(asks[0]["px"]); mid=(b0+a0)/2
        spread_bps=(a0-b0)/mid*1e4
        # depth within 10bps of mid
        lo=mid*(1-0.001); hi=mid*(1+0.001)
        bd=sum(fnum(l["px"])*fnum(l["sz"]) for l in bids if fnum(l["px"])>=lo)
        ad=sum(fnum(l["px"])*fnum(l["sz"]) for l in asks if fnum(l["px"])<=hi)
        # 1m realized vol over last 90 min
        c=post({"type":"candleSnapshot","req":{"coin":coin,"interval":"1m",
                "startTime":now_ms-90*60*1000,"endTime":now_ms}})
        rets=[]
        for i in range(1,len(c)):
            p0=fnum(c[i-1]["c"]); p1=fnum(c[i]["c"])
            if p0 and p1 and p0>0: rets.append(math.log(p1/p0))
        if len(rets)>2:
            m=sum(rets)/len(rets)
            var=sum((r-m)**2 for r in rets)/(len(rets)-1)
            rv_bps=math.sqrt(var)*1e4
        else: rv_bps=float('nan')
        cc=ctx_by_coin.get(coin,{})
        mark=fnum(cc.get("markPx")); oracle=fnum(cc.get("oraclePx")); prev=fnum(cc.get("prevDayPx"))
        disloc=(abs(mark-oracle)/oracle*1e4) if (mark and oracle) else float('nan')
        dm=((mark-prev)/prev*100) if (mark and prev) else float('nan')
        print(f"{coin:<14}{spread_bps:>12.2f}{bd/1e3:>18.1f}{ad/1e3:>18.1f}"
              f"{rv_bps:>11.2f}{disloc:>12.2f}{dm:>10.2f}")
    except Exception as e:
        print(f"{coin:<14}  [err: {e}]")
