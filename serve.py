#!/usr/bin/env python3
"""
serve.py — zero-dependency local server for the HyperCore Priority-Fee
Opportunity dashboard.

    python3 serve.py            # -> http://localhost:8787
    python3 serve.py --port 9000

Serves index.html plus a small JSON API that proxies + caches the public
Hyperliquid info API (the browser can't call it directly: no CORS). All
read-only; no auth; no funds.
"""
from __future__ import annotations
import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import hl_data

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(HERE, "vendor")


def safe_vendor_path(rel: str) -> str | None:
    """Resolve a /vendor/<rel> request to a real file, or None if it escapes the
    vendor dir. The trailing os.sep is required so siblings like `vendorEVIL`
    can't pass a bare prefix check."""
    fp = os.path.normpath(os.path.join(VENDOR_DIR, rel))
    if (fp == VENDOR_DIR or fp.startswith(VENDOR_DIR + os.sep)) and os.path.isfile(fp):
        return fp
    return None

# --------------------------------------------------------------------------- #
# Tiny thread-safe TTL cache. Serves last-good data if a refresh fails.
# --------------------------------------------------------------------------- #
class TTLCache:
    def __init__(self, max_entries: int = 512):
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, object]] = {}
        self._max = max_entries

    def get_or_compute(self, key: str, ttl: float, fn):
        now = time.time()
        with self._lock:
            hit = self._store.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1], "fresh"
        try:
            val = fn()
            with self._lock:
                self._store[key] = (now, val)
                # bound memory: caller-controlled `coin` keys could grow forever
                if len(self._store) > self._max:
                    for k in sorted(self._store, key=lambda k: self._store[k][0]
                                    )[:len(self._store) - self._max]:
                        del self._store[k]
            return val, "live"
        except Exception as e:
            if hit:                       # serve stale on failure
                return hit[1], f"stale (refresh failed: {e})"
            raise

CACHE = TTLCache()
MARKETS_TTL = 20.0     # seconds
DETAIL_TTL = 10.0


def markets_payload() -> dict:
    rows, _ = CACHE.get_or_compute(
        "markets", MARKETS_TTL,
        lambda: hl_data.fetch_all_markets())
    rows = [r for r in rows if r.get("disloc_bps") is not None]
    rows.sort(key=lambda r: -(r.get("pfi") or 0))
    return {"as_of_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "count": len(rows), "markets": rows}


def detail_payload(coin: str) -> dict:
    val, _ = CACHE.get_or_compute(
        f"detail:{coin}", DETAIL_TTL,
        lambda: hl_data.fetch_market_detail(coin))
    return val


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj: object):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif route.startswith("/vendor/"):
                fp = safe_vendor_path(route[len("/vendor/"):])
                if fp is None:
                    self._json(404, {"error": "not found"})
                else:
                    ctype = "application/javascript" if fp.endswith(".js") else "application/octet-stream"
                    with open(fp, "rb") as f:
                        self._send(200, f.read(), ctype)
            elif route == "/api/health":
                self._json(200, {"ok": True})
            elif route == "/api/markets":
                self._json(200, markets_payload())
            elif route == "/api/detail":
                qs = parse_qs(parsed.query)
                coin = (qs.get("coin") or [""])[0]
                if not coin:
                    self._json(400, {"error": "missing coin"})
                else:
                    self._json(200, detail_payload(coin))
            else:
                self._json(404, {"error": "not found"})
        except FileNotFoundError:
            self._json(500, {"error": "index.html not found next to serve.py"})
        except Exception as e:
            self._json(502, {"error": f"upstream error: {e}"})

    do_HEAD = do_GET

    def log_message(self, fmt, *args):  # quieter logs
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"HyperCore Priority-Fee dashboard -> {url}")
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        srv.shutdown()


if __name__ == "__main__":
    main()
