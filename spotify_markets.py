#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spotify 多地区（Multi-market）查询工具

支持三种用法（都能给出"目标地区的真实 ID"✅）：

  ① 按关键词搜索（先在一个区拿到 ISRC，再跨区批量找 ID ✅）
       python spotify_markets.py --query "周杰伦 晴天" --markets TW,HK,JP,US

  ② 按 ISRC 跨区检索（**最推荐** ✅ —— ISRC 全球唯一，各区 ID 可能不同 ✅）
       python spotify_markets.py --isrc TWK970300503 --markets TW,HK,JP,KR,US,CA

  ③ 已知 Track ID，检查它在各区是否可播（**Track Relinking** ✅）
       python spotify_markets.py --id 3n3Ppam7vgaVa1iaRUc9Lp --markets TW,JP,US

说明与注意（照官方文档 ✅）：
  · market 用 ISO 3166-1 alpha-2 代码（US / JP / HK / TW / GB …）✅
  · 同一首歌在不同地区可能 ID 不同 ✅，但 **ISRC 相同** ✅ → 用 ISRC 跨区最准 ✅
  · Track Relinking ✅：带 market 请求某 ID 时，若该区不可播但有替代版本，
    Spotify 返回 is_playable=true 且 **linked_from** 指向原 ID ✅
  · available_markets 字段 Spotify 已逐步废弃 ⚠️ → 建议用"逐区查询"来判定可用性 ✅
  · 429 限流 ✅ → 读取 **Retry-After** 退避重试 ✅（本工具已实现 ✅）

凭据（二选一，都会自动尝试 ✅）：
  A) 用户授权（PKCE ✅，程序目录里的 .spotify-token.json ✅ —— 由 serve_search.py 授权后生成 ✅）
  B) Client Credentials ✅：环境变量或 .env 里的
       SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, ".spotify-token.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SpotifyMarkets/1.0"
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"

DEFAULT_MARKETS = ["TW", "HK", "JP", "KR", "US", "CA", "GB", "SG", "MY", "AU"]

_print_lock = threading.Lock()


# ------------------------------------------------------------------ 凭据
def read_env_file() -> dict:
    env: dict[str, str] = {}
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return env
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def _creds() -> tuple[str | None, str | None]:
    env = read_env_file()
    cid = os.environ.get("SPOTIFY_CLIENT_ID") or env.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or env.get("SPOTIFY_CLIENT_SECRET")
    return cid or None, secret or None


def get_token() -> str:
    """① 先用 PKCE 用户 token ✅；② 退回 client credentials ✅。"""
    # ① 用户 token（含自动刷新 ✅）
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
                tok = json.load(fh)
            if tok.get("access_token") and tok.get("expires_at", 0) > time.time() + 30:
                return tok["access_token"]
            cid, _ = _creds()
            if cid and tok.get("refresh_token"):
                data = urllib.parse.urlencode(
                    {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid}
                ).encode()
                req = urllib.request.Request(
                    TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    fresh = json.loads(resp.read().decode())
                fresh["refresh_token"] = fresh.get("refresh_token") or tok.get("refresh_token")
                fresh["expires_at"] = time.time() + int(fresh.get("expires_in", 3600))
                with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
                    json.dump(fresh, fh, ensure_ascii=False, indent=2)
                return fresh["access_token"]
        except Exception:
            pass

    # ② client credentials ✅
    cid, secret = _creds()
    if not cid or not secret:
        raise SystemExit(
            "缺少 Spotify 凭据 ✅\n"
            "  · 方式 A：用 serve_search.py 的「登录 Spotify」授权一次（PKCE，不用 secret ✅）\n"
            "  · 方式 B：在 .env 里填 SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET ✅"
        )
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]


# ------------------------------------------------------------------ HTTP（含 429 退避 ✅）
def api_get(path: str, params: dict, token: str, retries: int = 3) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = 1.0
                try:
                    wait = float(exc.headers.get("Retry-After") or 1)
                except Exception:
                    pass
                time.sleep(min(wait, 10) + 0.2)
                continue
            try:
                return json.loads(exc.read().decode("utf-8", errors="replace"))
            except Exception:
                return {"error": {"status": exc.code, "message": str(exc)}}
        except Exception as exc:
            if attempt == retries:
                return {"error": {"status": 0, "message": str(exc)[:120]}}
            time.sleep(0.6)
    return {"error": {"status": 429, "message": "rate limited"}}


# ------------------------------------------------------------------ 三种查询
def search_one_market(term: str, market: str, token: str) -> dict:
    data = api_get("/search", {"q": term, "type": "track", "market": market, "limit": 1}, token)
    items = ((data.get("tracks") or {}).get("items")) or []
    if not items:
        return {"market": market, "available": False, "id": "", "isrc": ""}
    it = items[0]
    return {
        "market": market,
        "available": True,
        "id": it.get("id") or "",
        "name": it.get("name") or "",
        "artists": ", ".join(a.get("name", "") for a in it.get("artists") or []),
        "album": (it.get("album") or {}).get("name") or "",
        "isrc": ((it.get("external_ids") or {}).get("isrc")) or "",
        "uri": it.get("uri") or "",
    }


def isrc_in_market(isrc: str, market: str, token: str) -> dict:
    """按 ISRC 在指定区找真实 ID ✅（最推荐 ✅）。"""
    data = api_get("/search", {"q": f"isrc:{isrc}", "type": "track", "market": market, "limit": 1}, token)
    items = ((data.get("tracks") or {}).get("items")) or []
    if not items:
        return {"market": market, "available": False, "id": "", "isrc": isrc}
    it = items[0]
    return {
        "market": market,
        "available": True,
        "id": it.get("id") or "",
        "name": it.get("name") or "",
        "artists": ", ".join(a.get("name", "") for a in it.get("artists") or []),
        "album": (it.get("album") or {}).get("name") or "",
        "isrc": ((it.get("external_ids") or {}).get("isrc")) or isrc,
        "uri": it.get("uri") or "",
    }


def track_in_market(track_id: str, market: str, token: str) -> dict:
    """按 Track ID 查该区是否可播（**Track Relinking** ✅）。"""
    data = api_get(f"/tracks/{track_id}", {"market": market}, token)
    if "error" in data:
        return {"market": market, "available": False, "id": track_id, "error": (data["error"] or {}).get("message", "")}
    return {
        "market": market,
        "available": bool(data.get("is_playable", True)),
        "id": data.get("id") or track_id,
        "name": data.get("name") or "",
        "artists": ", ".join(a.get("name", "") for a in data.get("artists") or []),
        "isrc": ((data.get("external_ids") or {}).get("isrc")) or "",
        "is_relinked": "linked_from" in data,          # ✅ 发生了重定向
        "linked_from": (data.get("linked_from") or {}).get("id") or "",
        "restrictions": (data.get("restrictions") or {}).get("reason") or "",
    }


# ------------------------------------------------------------------ 主流程
def run(fn, markets: list[str], token: str, workers: int) -> list[dict]:
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 10))) as pool:
        futures = {pool.submit(fn, m, token): m for m in markets}
        for fut in concurrent.futures.as_completed(futures):
            m = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = {"market": m, "available": False, "id": "", "error": str(exc)[:80]}
            results.append(r)
    order = {m: i for i, m in enumerate(markets)}
    results.sort(key=lambda r: order.get(r.get("market", ""), 99))
    return results


def print_table(rows: list[dict], id_label: str) -> None:
    print(f"{'区':<4} {'可播':<4} {'ID':<24} {'ISRC':<14} {'重定向':<7} 曲名 — 艺人")
    print("-" * 110)
    for r in rows:
        ok = "✅" if r.get("available") else "❌"
        relink = r.get("linked_from") or ("是" if r.get("is_relinked") else "")
        print(
            f"{r.get('market',''):<4} {ok:<4} {(r.get('id') or '-'):<24} {(r.get('isrc') or '-'):<14} "
            f"{str(relink):<7} {r.get('name','')} — {r.get('artists','')}"
        )


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Spotify 多地区查询（关键词 / ISRC / Track ID + Track Relinking）")
    ap.add_argument("--query", help="关键词搜索（先在首区拿到 ISRC，再可配合 --isrc 跨区 ✅）")
    ap.add_argument("--isrc", help="按 ISRC 跨区检索（最推荐 ✅）")
    ap.add_argument("--id", help="按 Spotify Track ID 查各区可播性（Track Relinking ✅）")
    ap.add_argument("--markets", default=",".join(DEFAULT_MARKETS), help=f"地区列表（默认 {','.join(DEFAULT_MARKETS)}）")
    ap.add_argument("--workers", type=int, default=6, help="并发线程数（建议 5~10 ✅）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()]
    if not markets:
        markets = list(DEFAULT_MARKETS)

    token = get_token()
    rows: list[dict] = []

    if args.query:
        if len(markets) == 1:
            r = search_one_market(args.query, markets[0], token)
            r["market"] = markets[0]
            rows = [r]
        else:
            # 先在第一个区搜到 ISRC ✅ → 再用 ISRC 跨区（最准 ✅）
            first = search_one_market(args.query, markets[0], token)
            isrc = first.get("isrc") or ""
            with _print_lock:
                print(f"[1/2] {markets[0]} 区搜索：{first.get('name','')} — {first.get('artists','')}"
                      + (f"　ISRC={isrc} ✅ → 跨区检索中…" if isrc else "　（该区没搜到 ISRC，改为逐区关键词搜索 ⚠️）"))
            if isrc:
                rows = run(lambda m, t: isrc_in_market(isrc, m, t), markets, token, args.workers)
                rows[0] = first if first.get("market") == markets[0] else rows[0]
            else:
                rows = run(lambda m, t: search_one_market(args.query, m, t), markets, token, args.workers)
                rows[0]["market"] = markets[0]
    elif args.isrc:
        rows = run(lambda m, t: isrc_in_market(args.isrc, m, t), markets, token, args.workers)
    elif args.id:
        rows = run(lambda m, t: track_in_market(args.id, m, t), markets, token, args.workers)
    else:
        ap.print_help()
        sys.exit(2)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        avail = [r["market"] for r in rows if r.get("available")]
        print()
        print_table(rows, "ID")
        print()
        print(f"可播地区（{len(avail)}/{len(rows)}）：{','.join(avail) if avail else '（无）'}")
        ids = {r.get("id") for r in rows if r.get("available") and r.get("id")}
        if len(ids) > 1:
            print(f"⚠️ 各区 ID 不同（{len(ids)} 种）→ 若要做 TTML 元数据，请用**目标地区的那个 ID** ✅")
        relinked = [r for r in rows if r.get("linked_from") or r.get("is_relinked")]
        if relinked:
            print(f"↪️ 发生 Track Relinking 的地区：{','.join(r['market'] for r in relinked)} ✅")


if __name__ == "__main__":
    main()
