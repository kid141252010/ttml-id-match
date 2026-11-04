#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apple Music 官方 ISRC 查询（**免密钥** ✅）

原理：
    Apple Music 网页（music.apple.com）的 JS 里内嵌了一个长期有效的 JWT ✅
    → 用它调 amp-api.music.apple.com/v1/catalog/{区}/songs/{id} ✅
    → 得到 attributes.isrc ✅（官方出处 ✅）

用法：
    python apple_isrc.py 535824738                # 默认 us 区
    python apple_isrc.py 535824738 tw             # 指定区（tw/hk/jp/kr/us/ca…）
    python apple_isrc.py 535824738 tw --json      # 输出 JSON（含更多字段）
    python apple_isrc.py --batch ids.txt          # 每行一个 ID（可选 "id 区"）

注意：
    · token 会周期性轮换 ✅ → 本工具自动缓存 6 小时 + 失败自动强刷 ✅
    · ISRC 全球唯一 ✅ → 任意区查到的都一样 ✅（美国区通常最全 ✅）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
AMP_API = "https://amp-api.music.apple.com/v1/catalog"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}")
SCRIPT_RE = re.compile(r'src="([^"]+\.js)"')

_cache: dict = {"token": None, "ts": 0.0}


def _get(url: str, headers: dict | None = None, timeout: int = 40) -> str:
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def web_token(force: bool = False) -> str | None:
    """从 Apple Music 网页里取内嵌 JWT ✅（缓存 6 小时 ✅）。"""
    now = time.time()
    if not force and _cache["token"] and now - _cache["ts"] < 6 * 3600:
        return _cache["token"]
    try:
        html = _get("https://music.apple.com/us/song/1", {"Accept": "text/html"})
    except Exception:
        return None
    for s in SCRIPT_RE.findall(html)[:10]:
        url = s if s.startswith("http") else urllib.parse.urljoin("https://music.apple.com/", s)
        try:
            body = _get(url)
        except Exception:
            continue
        m = JWT_RE.search(body)
        if m:
            _cache["token"] = m.group(0)
            _cache["ts"] = now
            return _cache["token"]
    return None


def lookup(song_id: str, storefront: str = "us", with_extra: bool = False) -> dict:
    """查一首歌的官方信息 ✅（至少含 isrc ✅）。

    注意：Apple 的**歌曲 ID 是分区的** ✅（同一首歌各区 ID 不同 ✅）
    → 所以在哪个区搜到的 ID，就用那个区查最稳 ✅
    → 本函数会**依次尝试**：指定区 → 常见区（tw/hk/jp/kr/ca/us/gb/sg）✅
    """
    song_id = str(song_id).strip()
    if not song_id:
        return {"id": "", "isrc": "", "error": "empty id"}

    token = web_token()
    if not token:
        return {"id": song_id, "isrc": "", "error": "无法获取 Apple 网页 token"}

    tried: list[str] = []
    order = [storefront] + [s for s in ("tw", "hk", "jp", "kr", "ca", "us", "gb", "sg") if s != storefront]

    last_error = ""
    for sf in order:
        sf = (sf or "us").strip().lower()
        if sf in tried:
            continue
        tried.append(sf)
        url = f"{AMP_API}/{sf}/songs/{song_id}"
        for attempt in (1, 2):
            try:
                body = _get(
                    url,
                    {
                        "Authorization": f"Bearer {token}",
                        "Origin": "https://music.apple.com",
                        "Referer": "https://music.apple.com/",
                        "Accept": "application/json",
                    },
                )
                data = json.loads(body)
                for item in data.get("data", []) or []:
                    attr = item.get("attributes") or {}
                    out = {
                        "id": song_id,
                        "storefront": sf,
                        "isrc": attr.get("isrc") or "",
                        "name": attr.get("name") or "",
                        "artist": attr.get("artistName") or "",
                        "album": attr.get("albumName") or "",
                        "duration_ms": attr.get("durationInMillis") or 0,
                        "release_date": attr.get("releaseDate") or "",
                        "url": attr.get("url") or "",
                        "tried": tried,
                    }
                    if with_extra:
                        out["composer"] = attr.get("composerName") or ""
                        out["genre"] = (attr.get("genreNames") or [None])[0] or ""
                        out["copyright"] = attr.get("copyright") or ""
                    return out
                last_error = "返回里没有这首歌"
                break
            except Exception as exc:
                last_error = str(exc)[:120]
                if attempt == 1:
                    token = web_token(force=True)  # token 可能过期 → 强刷一次 ✅
                    if not token:
                        return {"id": song_id, "isrc": "", "error": "token 刷新失败"}
                    continue
                break
    return {"id": song_id, "isrc": "", "error": last_error or "unknown", "tried": tried}


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Apple Music 官方 ISRC 查询（免密钥）")
    parser.add_argument("song_id", nargs="?", help="Apple Music 歌曲 ID（就是搜索结果里的 trackId）")
    parser.add_argument("storefront", nargs="?", default="us", help="区（默认 us；tw/hk/jp/kr/ca…）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--extra", action="store_true", help="额外字段（作曲/流派/版权）")
    parser.add_argument("--batch", help="批量：文件里每行一个 ID（可写 'id 区'）")
    args = parser.parse_args()

    if args.batch:
        results = []
        with open(args.batch, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                sid = parts[0]
                sf = parts[1] if len(parts) > 1 else "us"
                r = lookup(sid, sf, args.extra)
                results.append(r)
                print(f"{sid}\t{r.get('storefront','')}\t{r.get('isrc','') or '-'}\t{r.get('name','')}\t{r.get('artist','')}")
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if not args.song_id:
        parser.print_help()
        sys.exit(2)

    r = lookup(args.song_id, args.storefront, args.extra)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        if r.get("isrc"):
            print(f"ISRC: {r['isrc']}")
            print(f"曲名: {r.get('name','')}  ·  艺人: {r.get('artist','')}  ·  专辑: {r.get('album','')}")
        else:
            print(f"未取到 ISRC（{r.get('error','')}）")


if __name__ == "__main__":
    main()
