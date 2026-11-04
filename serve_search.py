#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音乐元数据搜索 —— 本地网页版（B）服务器

功能：
  GET /                                   → 前端页面 ✅
  GET /api/search?term=...&countries=TW,KR&limit=10&sources=apple,spotify
                                          → 多源 + 多地区搜索，跨区合并 ✅
  GET /api/status                         → 看看 Spotify 凭据有没有配好 ✅

Spotify 凭据（任选其一）：环境变量 SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET，
或同目录 .env 文件（键名同 .env.example ✅）。未配置时只搜 Apple ✅。

用法：
    python serve_search.py                 # 127.0.0.1:8765，自动打开浏览器
    python serve_search.py --port 9000 --no-open
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "search_metadata.html")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MusicMetadataSearch/1.0"
ITUNES_SEARCH = "https://itunes.apple.com/search"
SPOTIFY_TOKEN = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH = "https://api.spotify.com/v1/search"
DEEZER_SEARCH = "https://api.deezer.com/search"      # ✅ 公开接口，无需密钥，含 ISRC
MUSICBRAINZ_SEARCH = "https://musicbrainz.org/ws/2/recording"   # ✅ 公开接口，无需密钥
# MusicBrainz 要求 UA 里带联系方式 ✅（换成你自己的邮箱更礼貌 ✅）
MUSICBRAINZ_UA = "MusicMetadataSearch/1.0 ( mailto:music-meta-search@example.com )"


# ------------------------------------------------------------------ 工具
def _get_json(url: str, headers: dict | None = None, timeout: int = 30) -> dict:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


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


# 内置一个**公共** client id 作默认值 ✅（让"点一下就能登录"成为可能 ✅）
# 你可以在 .env 里用 SPOTIFY_CLIENT_ID 覆盖它 ✅
DEFAULT_SPOTIFY_CLIENT_ID = "65b708073fc0480ea92a077233ca87bd"


def spotify_credentials() -> tuple[str | None, str | None]:
    """返回 (client_id, client_secret)。
    client_id 永不为空 ✅（没有配置时用内置公共值 ✅ → 至少能试登录 ✅）；
    client_secret 只有你填了才有 ✅（PKCE 不需要它 ✅）。
    """
    env = read_env_file()
    cid = os.environ.get("SPOTIFY_CLIENT_ID") or env.get("SPOTIFY_CLIENT_ID") or DEFAULT_SPOTIFY_CLIENT_ID
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or env.get("SPOTIFY_CLIENT_SECRET")
    return cid or None, secret or None


def _spotify_credentials_legacy() -> tuple[str | None, str | None]:
    env = read_env_file()
    cid = os.environ.get("SPOTIFY_CLIENT_ID") or env.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or env.get("SPOTIFY_CLIENT_SECRET")
    return cid or None, secret or None


def spotify_token() -> str | None:
    # ① 优先用**用户授权**的 token ✅（PKCE ✅ —— 可指定任意地区 ✅）
    try:
        user = spotify_user_access_token()
        if user:
            return user
    except Exception:
        pass
    # ② 退回 client credentials ✅（只用 id+secret ✅）
    cid, secret = spotify_credentials()
    if not cid or not secret:
        return None
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        SPOTIFY_TOKEN,
        data=data,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]


# ------------------------------------------------------------------ 搜索
def apple_search(term: str, country: str, limit: int) -> list[dict]:
    # ★ 首选：Apple Music 官方目录接口 amp-api ✅
    #   · 任何地区都能用（**含 CN 中国大陆** ✅ —— iTunes 那个接口对 CN 基本返回空 ❌）
    #   · 返回里**直接带 ISRC** ✅（就不用再逐首补了 ✅）
    #   · 失败/无结果 → 自动回退到下面的 iTunes 公开搜索 ✅
    try:
        import apple_isrc as _ai  # ✅ 已验证可用的 token 模块（从网页 JS 里挖长期 JWT ✅）

        tok = _ai.web_token()
        if tok:
            # ⚠️ 曾尝试"5 个候选区并行"，实测反而更慢/卡住（线程池套线程池 ❌）→ 回退为串行 ✅
            #    但保留 **20 秒短超时** ✅（原来 60 秒，慢区会拖很久 ✅）
            first = (country or "us").lower()
            order = [first] + [x for x in ("us", "jp", "tw", "hk") if x != first]
            for sf in order:
                q = urllib.parse.urlencode({"term": term, "types": "songs", "limit": limit})
                url = f"{AMP_API}/{sf}/search?{q}"
                try:
                    data = _get_json(
                        url,
                        headers={
                            "Authorization": f"Bearer {tok}",
                            "Origin": "https://music.apple.com",
                            "Referer": "https://music.apple.com/",
                            "Accept": "application/json",
                        },
                        timeout=20,
                    )
                except Exception as exc:
                    APPLE_SEARCH_ERRORS.append(f"{sf}: {str(exc)[:90]}")
                    continue
                songs = (((data.get("results") or {}).get("songs") or {}).get("data")) or []
                if not songs:
                    APPLE_SEARCH_ERRORS.append(f"{sf}: 0 results")
                    continue
                out = []
                for s in songs[:limit]:
                    a = s.get("attributes") or {}
                    art = a.get("artwork") or {}
                    out.append(
                        {
                            "source": "apple",
                            "name": a.get("name") or "",
                            "artist": a.get("artistName") or "",
                            "album": a.get("albumName") or "",
                            "duration_ms": int(a.get("durationInMillis") or 0),
                            "track_id": s.get("id") or "",
                            "isrc": a.get("isrc") or "",          # ★★ 官方 ISRC ✅
                            "genre": (a.get("genreNames") or [""])[0],
                            "release_date": (a.get("releaseDate") or "")[:10],
                            "artwork": (art.get("url") or "").replace("{w}", "100").replace("{h}", "100"),
                            "url": a.get("url") or "",
                            "countries": [country],
                        }
                    )
                return out
    except Exception:
        pass

    # ② 回退：iTunes 公开搜索 ✅（快、无需 token ✅；但**没有 ISRC** ✅，CN 区也基本搜不到 ⚠️）
    params = urllib.parse.urlencode(
        {"term": term, "media": "music", "entity": "song", "limit": limit, "country": country}
    )
    data = _get_json(f"{ITUNES_SEARCH}?{params}")
    out = []
    for r in data.get("results", []):
        if r.get("kind") and r.get("kind") != "song":
            continue
        out.append(
            {
                "source": "apple",
                "name": r.get("trackName") or "",
                "artist": r.get("artistName") or "",
                "album": r.get("collectionName") or "",
                "duration_ms": int(r.get("trackTimeMillis") or 0),
                "track_id": str(r.get("trackId") or ""),
                "isrc": "",
                "genre": r.get("primaryGenreName") or "",
                "release_date": (r.get("releaseDate") or "")[:10],
                "artwork": r.get("artworkUrl100") or "",
                "url": r.get("trackViewUrl") or "",
                "countries": [country],
            }
        )
    return out


def spotify_search(term: str, market: str, limit: int, token: str) -> list[dict]:
    params = urllib.parse.urlencode({"q": term, "type": "track", "market": market, "limit": limit})
    data = _get_json(f"{SPOTIFY_SEARCH}?{params}", headers={"Authorization": f"Bearer {token}"})
    out = []
    for r in (data.get("tracks") or {}).get("items") or []:
        album = r.get("album") or {}
        images = album.get("images") or []
        ext = r.get("external_ids") or {}
        out.append(
            {
                "source": "spotify",
                "name": r.get("name") or "",
                "artist": ", ".join(a.get("name", "") for a in r.get("artists") or []),
                "album": album.get("name") or "",
                "duration_ms": int(r.get("duration_ms") or 0),
                "track_id": r.get("id") or "",
                "isrc": ext.get("isrc") or "",          # ✅ ISRC
                "genre": "",
                "release_date": (album.get("release_date") or "")[:10],
                "artwork": (images[0].get("url") if images else "") or "",
                "url": (r.get("external_urls") or {}).get("spotify") or "",
                "countries": [market],
            }
        )
    return out


def deezer_search(term: str, market: str, limit: int) -> list[dict]:
    """Deezer 公开 API ✅（**无需任何密钥** ✅，且**返回 ISRC** ✅）。"""
    params = urllib.parse.urlencode({"q": term, "limit": limit})
    data = _get_json(f"{DEEZER_SEARCH}?{params}")
    out = []
    for r in data.get("data", [])[:limit]:
        album = r.get("album") or {}
        artist = r.get("artist") or {}
        out.append(
            {
                "source": "deezer",
                "name": r.get("title") or "",
                "artist": artist.get("name") or "",
                "album": album.get("title") or "",
                "duration_ms": int(r.get("duration") or 0) * 1000,
                "track_id": str(r.get("id") or ""),
                "isrc": r.get("isrc") or "",          # ✅ Deezer 直接给 ISRC
                "genre": "",
                "release_date": "",
                "artwork": album.get("cover_medium") or album.get("cover") or "",
                "url": r.get("link") or "",
                "countries": [market],
            }
        )
    return out


QQ_SEARCH = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"   # ✅ 免密钥
NETEASE_SEARCH = "https://music.163.com/api/search/get/web"       # ✅ 免密钥


def qq_search(term: str, market: str, limit: int) -> list[dict]:
    """QQ音乐（免密钥 ✅）—— songmid = QQ 歌曲 ID ✅，albummid = 专辑 ID ✅。"""
    params = urllib.parse.urlencode({
        "w": term, "format": "json", "p": 1, "n": max(1, min(limit, 20)),
        "aggr": 1, "cr": 1, "new_json": 1, "inCharset": "utf8", "outCharset": "utf-8",
    })
    data = _get_json(f"{QQ_SEARCH}?{params}", headers={"Referer": "https://y.qq.com/"})
    out = []
    for s in ((((data.get("data") or {}).get("song") or {}).get("list")) or []):
        al = s.get("album") or {}
        mid = s.get("mid") or s.get("songmid") or ""
        out.append({
            "source": "qq",
            "name": s.get("title") or s.get("songname") or "",
            "artist": "/".join(x.get("name", "") for x in (s.get("singer") or [])),
            "album": al.get("name") or "",
            "duration_ms": int(s.get("interval") or 0) * 1000,
            "track_id": mid,                                    # songmid（字母数字）✅
            "song_id": str(s.get("id") or s.get("songid") or ""),   # ★ 全数字 songid ✅
            "album_id": al.get("mid") or "",                     # albummid ✅
            "album_id_num": str(al.get("id") or ""),             # ★ 全数字 album id ✅
            "isrc": "",
            "genre": "",
            "release_date": (s.get("time_public") or "")[:10],
            # ★ QQ 封面：用 150x150 小图 ✅（越大越慢；之前是空字符串 ❌ → 显示空白 ✅）
            "artwork": (f"https://y.qq.com/music/photo_new/T002R150x150M000{al.get('mid')}.jpg"
                        if al.get("mid") else ""),
            "url": f"https://y.qq.com/n/ryqq/songDetail/{mid}",
            "countries": [market] if market else [],
        })
    return out


def netease_search(term: str, market: str, limit: int) -> list[dict]:
    """网易云音乐（免密钥 ✅）—— 歌曲 id ✅ + 专辑 album.id ✅。"""
    params = urllib.parse.urlencode({"s": term, "type": 1, "limit": max(1, min(limit, 20)), "offset": 0})
    data = _get_json(
        f"{NETEASE_SEARCH}?{params}",
        headers={"Referer": "https://music.163.com/", "Cookie": "appver=2.0.2"},
    )
    songs = ((data.get("result") or {}).get("songs")) or []
    # 网易云会把翻唱排前面 ⚠️ → 按热度降序，尽量让原唱靠前 ✅
    try:
        songs.sort(key=lambda s: (s.get("popularity") or 0), reverse=True)
    except Exception:
        pass
    out = []
    for s in songs:
        al = s.get("album") or {}
        sid = str(s.get("id") or "")
        out.append({
            "source": "netease",
            "name": s.get("name") or "",
            "artist": "/".join(a.get("name", "") for a in (s.get("artists") or [])),
            "album": al.get("name") or "",
            "duration_ms": int(s.get("duration") or 0),
            "track_id": sid,
            "album_id": str(al.get("id") or ""),
            "isrc": "",
            "genre": "",
            "release_date": "",
            # ★ 网易云封面：加 ?param=100y100 取缩略图 ✅（原图很大很慢 ❌）
            "artwork": ((al.get("picUrl") or "").split("?")[0] + "?param=100y100"
                        if al.get("picUrl") else ""),
            "url": f"https://music.163.com/#/song?id={sid}",
            "countries": [market] if market else [],
        })
    return out


def musicbrainz_search(term: str, market: str, limit: int) -> list[dict]:
    """MusicBrainz 公开接口 ✅（**无需密钥** ✅）。
    要求：User-Agent 带联系方式 ✅ + 限速 1 req/s ✅（这里每个地区只请求一次，天然满足 ✅）。
    """
    params = urllib.parse.urlencode({"query": term, "fmt": "json", "limit": limit})
    data = _get_json(
        f"{MUSICBRAINZ_SEARCH}?{params}",
        headers={"User-Agent": MUSICBRAINZ_UA, "Accept": "application/json"},
    )
    out = []
    for r in data.get("recordings", [])[:limit]:
        artists = " & ".join(a.get("name", "") for a in r.get("artist-credit", []) if a.get("name"))
        releases = r.get("releases") or []
        album = (releases[0].get("title") if releases else "") or ""
        date = (r.get("first-release-date") or (releases[0].get("date") if releases else "") or "")[:10]
        out.append(
            {
                "source": "musicbrainz",
                "name": r.get("title") or "",
                "artist": artists,
                "album": album,
                "duration_ms": int(r.get("length") or 0),
                "track_id": r.get("id") or "",
                "isrc": "",          # 单次搜索不返回 ISRC（要再查一次 recording ✅，为守限速这里省略）
                "genre": "",
                "release_date": date,
                "artwork": "",
                "url": f"https://musicbrainz.org/recording/{r.get('id')}" if r.get("id") else "",
                "countries": [market],
            }
        )
    return out


# ------------------------------------------------------------------ Spotify PKCE 用户授权
# 流程（和 Lyricify 一个路子 ✅）：
#   1) 浏览器打开 /api/spotify/login  → 302 到 Spotify 授权页（带 code_challenge ✅）
#   2) 用户同意后回跳 /callback?code=… → 用 code + code_verifier 换 token（**不需要 secret** ✅）
#   3) token 存到本地 .spotify-token.json ✅，过期自动用 refresh_token 刷新 ✅
#   4) 之后搜索带**用户 token** → 可以指定**任意 market**（TW/KR/CA… ✅）
SPOTIFY_AUTHORIZE = "https://accounts.spotify.com/authorize"
SPOTIFY_REDIRECT = os.environ.get("SPOTIFY_REDIRECT", "http://127.0.0.1:8765/callback")  # ✅ 必须与 App 里登记的回调一致
SPOTIFY_SCOPE = "user-read-private"          # 只需要能读公开目录即可 ✅
TOKEN_FILE = os.path.join(HERE, ".spotify-token.json")
_pkce_verifier: str | None = None            # 本次登录的 code_verifier（进程内保存 ✅）


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def pkce_begin() -> tuple[str, str]:
    """生成 (verifier, challenge) ✅ —— S256 ✅。"""
    global _pkce_verifier
    verifier = _b64url(os.urandom(64))
    challenge = _b64url(__import__("hashlib").sha256(verifier.encode()).digest())
    _pkce_verifier = verifier
    return verifier, challenge


def load_user_token() -> dict | None:
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def save_user_token(data: dict) -> None:
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def spotify_user_access_token() -> str | None:
    """拿用户 token（过期就刷新 ✅）；没有授权过返回 None ✅。"""
    cid, secret = spotify_credentials()
    if not cid:
        return None
    tok = load_user_token()
    if not tok:
        return None
    if tok.get("expires_at", 0) > __import__("time").time() + 30 and tok.get("access_token"):
        return tok["access_token"]
    refresh = tok.get("refresh_token")
    if not refresh:
        return None
    data = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid}
    ).encode()
    req = urllib.request.Request(
        SPOTIFY_TOKEN,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        fresh = json.loads(resp.read().decode())
    fresh["refresh_token"] = fresh.get("refresh_token") or refresh
    fresh["expires_at"] = __import__("time").time() + int(fresh.get("expires_in", 3600))
    save_user_token(fresh)
    return fresh.get("access_token")


# ------------------------------------------------------------------ Apple Music 官方 ISRC（免密钥 ✅）
# 原理：music.apple.com 页面 JS 里内嵌长期 JWT ✅ → 调 amp-api ✅ → attributes.isrc ✅
AMP_API = "https://amp-api.music.apple.com/v1/catalog"
# amp-api 搜索过程中的错误记录（会随 /api/search 的 warnings 一起返回 ✅，方便定位 KR/CN 问题 ✅）
APPLE_SEARCH_ERRORS: list[str] = []
APPLE_JWT_RE = r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}"
_apple_token = {"v": None, "ts": 0.0}


def apple_isrc_lookup(song_id: str, storefront: str = "us") -> str:
    """取 Apple 官方 ISRC ✅ —— 直接复用已验证可用的 apple_isrc.py ✅（多区回退在里面 ✅）。"""
    try:
        import apple_isrc as _ai
        r = _ai.lookup(song_id, storefront or "us")
        return (r or {}).get("isrc") or ""
    except Exception:
        return ""


def apple_web_token(force: bool = False):
    """（保留给外部调用；实际取 token 的逻辑在 apple_isrc.py 里 ✅）"""
    try:
        import apple_isrc as _ai
        return _ai.web_token(force=force)
    except Exception:
        return None


def merge_search(term: str, sources: list[str], countries: list[str], limit: int) -> dict:
    merged: dict[str, dict] = {}
    failed: list[str] = []
    warnings: list[str] = []
    token = None

    # ★ QQ音乐 / 网易云 / Deezer / MusicBrainz 跟地区无关 ✅
    #   如果只选了这些源，**允许一个地区都不勾** ✅（用一个占位地区让循环跑起来 ✅）
    regionless = {"qq", "netease", "deezer", "musicbrainz"}
    if not countries and any(s in regionless for s in sources):
        countries = ["--"]
        warnings.append("未选地区：QQ音乐 / 网易云 / Deezer / MusicBrainz 无需地区 ✅")

    if "spotify" in sources:
        try:
            token = spotify_token()
        except Exception as exc:
            warnings.append(f"Spotify 取 token 失败：{exc}")
        if not token:
            warnings.append("Spotify 未配置凭据（SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET）→ 已跳过")
            sources = [s for s in sources if s != "spotify"]

    # ★★ 并行抓取（原来是串行，5 个源一个一个来 → 很慢 ❌）
    #     现在所有 (地区 × 数据源) 同时发请求 ✅ → 总耗时 ≈ 最慢的那个 ✅
    import concurrent.futures as _cf

    tasks: list[tuple[str, str]] = [(c, s) for c in countries for s in sources]

    def _fetch(task: tuple[str, str]):
        country, source = task
        try:
            if source == "apple":
                return apple_search(term, country, limit)
            if source == "deezer":
                return deezer_search(term, country, limit) if country == countries[0] else []
            if source == "musicbrainz":
                return musicbrainz_search(term, country, limit) if country == countries[0] else []
            if source == "qq":
                return qq_search(term, country, limit) if country == countries[0] else []
            if source == "netease":
                return netease_search(term, country, limit) if country == countries[0] else []
            if source == "spotify" and token:
                return spotify_search(term, country, limit, token)
            return []
        except Exception as exc:                     # 单个源失败不影响其它源 ✅
            return ("__error__", f"{source}/{country} 失败：{exc}")

    with _cf.ThreadPoolExecutor(max_workers=8) as _pool:
        fetched = list(_pool.map(_fetch, tasks))

    for (country, source), found in zip(tasks, fetched):
        if isinstance(found, tuple) and found and found[0] == "__error__":
            failed.append(f"{source}/{country}")
            warnings.append(found[1])
            continue
        for t in found:
            key = f"{t['source']}|{t['name'].strip().lower()}|{t['album'].strip().lower()}"
            if key.endswith("|"):
                continue
            if key in merged:
                if country not in merged[key]["countries"]:
                    merged[key]["countries"].append(country)
                if not merged[key].get("isrc") and t.get("isrc"):
                    merged[key]["isrc"] = t["isrc"]
            else:
                merged[key] = t

    # ★ APPLE_ISRC_ENRICH：给 Apple 结果补官方 ISRC ✅（最多 8 条，避免拖慢 ✅）
    if "apple" in sources:
        need = [r for r in merged.values() if r.get("source") == "apple" and not r.get("isrc")]
        for r in need[:8]:
            try:
                sf = (r.get("countries") or ["us"])[0]
                got = apple_isrc_lookup(r.get("track_id", ""), sf)
                if got:
                    r["isrc"] = got
            except Exception:
                pass

    # ★ 把 Apple 搜索过程中记录的错误并进 warnings ✅（否则用户看不到原因 ❌）
    try:
        warnings.extend(APPLE_SEARCH_ERRORS[-12:])
    except Exception:
        pass

    return {"count": len(merged), "failed": failed, "warnings": warnings, "results": list(merged.values())}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "MusicMetaSearch/2.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_OPTIONS(self):  # noqa: N802
        self._send(204, b"", "text/plain; charset=utf-8")

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            if not os.path.exists(PAGE):
                self._send(404, b"search_metadata.html not found", "text/plain; charset=utf-8")
                return
            with open(PAGE, "rb") as fh:
                self._send(200, fh.read(), "text/html; charset=utf-8")
            return

        if parsed.path == "/api/status":
            cid, secret = spotify_credentials()
            tok = load_user_token()
            self._json(
                {
                    "spotify": bool(cid and secret),
                    "spotify_client_id": bool(cid),
                    "spotify_logged_in": bool(tok and tok.get("refresh_token")),
                }
            )
            return

        # ---- Spotify PKCE 用户授权 ✅ ----
        if parsed.path == "/api/spotify/login":
            cid, _ = spotify_credentials()
            if not cid:
                self._json({"error": "缺少 SPOTIFY_CLIENT_ID（请在 .env 里填 ✅）"}, 400)
                return
            _, challenge = pkce_begin()
            qs = urllib.parse.urlencode(
                {
                    "client_id": cid,
                    "response_type": "code",
                    "redirect_uri": SPOTIFY_REDIRECT,
                    "scope": SPOTIFY_SCOPE,
                    "code_challenge_method": "S256",
                    "code_challenge": challenge,
                }
            )
            self.send_response(302)
            self.send_header("Location", f"{SPOTIFY_AUTHORIZE}?{qs}")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return

        if parsed.path == "/callback":
            qs = urllib.parse.parse_qs(parsed.query)
            code = (qs.get("code") or [""])[0]
            err = (qs.get("error") or [""])[0]
            if err:
                self._send(400, f"授权被拒绝：{err}".encode("utf-8"), "text/plain; charset=utf-8")
                return
            cid, _ = spotify_credentials()
            if not code or not cid or not _pkce_verifier:
                self._send(400, "授权回调缺少参数（请从 /api/spotify/login 重新开始 ✅）".encode("utf-8"), "text/plain; charset=utf-8")
                return
            try:
                data = urllib.parse.urlencode(
                    {
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": SPOTIFY_REDIRECT,
                        "client_id": cid,
                        "code_verifier": _pkce_verifier,
                    }
                ).encode()
                req = urllib.request.Request(
                    SPOTIFY_TOKEN,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    fresh = json.loads(resp.read().decode())
                fresh["expires_at"] = __import__("time").time() + int(fresh.get("expires_in", 3600))
                save_user_token(fresh)
                body = "<h2>✅ Spotify 授权成功</h2><p>可以关闭本页，回到搜索页面刷新即可 ✅</p>"
                self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")
            except Exception as exc:
                self._send(500, f"换取 token 失败：{exc}".encode("utf-8"), "text/plain; charset=utf-8")
            return

        if parsed.path == "/api/spotify-markets":
            qs = urllib.parse.parse_qs(parsed.query)
            markets = [m.strip().upper() for m in (qs.get("markets") or ["TW,HK,JP,KR,US,CA"])[0].split(",") if m.strip()]
            isrc = (qs.get("isrc") or [""])[0].strip()
            tid = (qs.get("id") or [""])[0].strip()
            query = (qs.get("query") or [""])[0].strip()
            if not (isrc or tid or query):
                self._json({"error": "需要 isrc / id / query 之一"}, 400)
                return
            try:
                import spotify_markets as _sm
                token = _sm.get_token()
                if isrc:
                    rows = _sm.run(lambda m, t: _sm.isrc_in_market(isrc, m, t), markets, token, 6)
                elif tid:
                    rows = _sm.run(lambda m, t: _sm.track_in_market(tid, m, t), markets, token, 6)
                else:
                    first = _sm.search_one_market(query, markets[0], token)
                    got = first.get("isrc") or ""
                    if got:
                        rows = _sm.run(lambda m, t: _sm.isrc_in_market(got, m, t), markets, token, 6)
                    else:
                        rows = _sm.run(lambda m, t: _sm.search_one_market(query, m, t), markets, token, 6)
                avail = [r["market"] for r in rows if r.get("available")]
                self._json({"markets": markets, "count": len(rows), "available": avail, "results": rows})
            except SystemExit as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:
                self._json({"error": str(exc)[:200]}, 500)
            return
        if parsed.path == "/api/spotify-link":
            # ✅ 浏览器不能改 UA，MusicBrainz 会 403 ❌ → 由服务器代查（带合规 UA ✅）
            qs = urllib.parse.parse_qs(parsed.query)
            name = (qs.get("name") or [""])[0].strip()
            artist = (qs.get("artist") or [""])[0].strip()
            if not name:
                self._json({"error": "missing name"}, 400)
                return
            MB_UA = {"User-Agent": "MusicMetaSearch/1.1 ( https://github.com/zhr166/ttml-id-match- )"}
            found: list[dict] = []
            err = ""
            try:
                q = f'recording:"{name}"'
                if artist:
                    q += f' AND artist:"{artist}"'
                url = "https://musicbrainz.org/ws/2/recording/?" + urllib.parse.urlencode(
                    {"query": q, "fmt": "json", "limit": 3})
                recs = _get_json(url, headers=MB_UA, timeout=30).get("recordings") or []
                for r in recs:
                    try:
                        import time as _t
                        _t.sleep(0.35)          # MusicBrainz 限速 1 req/s ✅
                    except Exception:
                        pass
                    u2 = (f"https://musicbrainz.org/ws/2/recording/{r['id']}"
                          "?inc=url-rels&fmt=json")
                    rels = _get_json(u2, headers=MB_UA, timeout=30).get("relations") or []
                    for rel in rels:
                        res = ((rel.get("url") or {}).get("resource")) or ""
                        if "open.spotify.com" in res:
                            found.append({"title": r.get("title") or "",
                                          "mbid": r.get("id") or "",
                                          "url": res,
                                          "id": res.rstrip("/").split("/")[-1].split("?")[0]})
            except Exception as exc:
                err = str(exc)[:120]
            self._json({"name": name, "artist": artist, "count": len(found),
                        "error": err, "links": found})
            return

        if parsed.path == "/api/apple-isrc":
            qs = urllib.parse.parse_qs(parsed.query)
            sid = (qs.get("id") or [""])[0].strip()
            sf = ((qs.get("storefront") or ["us"])[0] or "us").strip().lower()
            if not sid:
                self._json({"error": "missing id"}, 400)
                return
            self._json({"id": sid, "storefront": sf, "isrc": apple_isrc_lookup(sid, sf)})
            return
        if parsed.path == "/api/spotify/logout":
            try:
                if os.path.exists(TOKEN_FILE):
                    os.remove(TOKEN_FILE)
            except Exception:
                pass
            self._json({"ok": True})
            return

        if parsed.path == "/api/search":
            qs = urllib.parse.parse_qs(parsed.query)
            term = (qs.get("term") or [""])[0].strip()
            # ★ 注意：网页没勾地区时 countries 参数为空 ✅
            #   原来是 `or ["TW,HK,JP,KR,US,CA"]` ❌ → 空参数会被当成"默认六区" ✅
            #   → QQ/网易云 结果都被打上 TW 标签（甩不掉的"狗皮膏药"）❌
            #   现在默认改为空 ✅ → 交给 merge_search 的"无地区"逻辑处理 ✅
            countries = [c.strip().upper() for c in (qs.get("countries") or [""])[0].split(",") if c.strip()]
            sources = [s.strip().lower() for s in (qs.get("sources") or ["apple"])[0].split(",") if s.strip()]
            try:
                limit = max(1, min(50, int((qs.get("limit") or ["10"])[0])))
            except ValueError:
                limit = 10
            if not term:
                self._json({"error": "missing term"}, 400)
                return
            try:
                self._json(merge_search(term, sources, countries, limit))
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")


def main() -> None:
    # Windows 控制台默认 GBK ❌ → print 里的 ✅ 等符号会抛 UnicodeEncodeError 导致整个程序崩溃 ✅
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="音乐元数据搜索 本地网页版服务器（Apple + Spotify，多地区）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    cid, secret = spotify_credentials()
    print(f"音乐元数据搜索已启动 ✅  {url}")
    print("Spotify：已配置 ✅" if (cid and secret) else "Spotify：未配置（只搜 Apple ✅；要启用请在 .env 填 SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET）")
    print("（按 Ctrl+C 停止）")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止 ✅")
        httpd.server_close()


if __name__ == "__main__":
    main()
