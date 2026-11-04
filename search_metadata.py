#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音乐元数据搜索（独立程序，与 AMLL Tool 无关）—— 支持 Apple Music 与 Spotify

用法：
    python search_metadata.py "周杰伦 晴天"                       # 默认 Apple Music
    python search_metadata.py "hoyomix" --source apple,spotify     # 两个源都搜
    python search_metadata.py "晴天" --country TW,HK,JP,US,CA,KR   # 指定地区
    python search_metadata.py "晴天" --json
    python search_metadata.py "晴天" --copy

Apple Music：iTunes 公开接口，**无需密钥** ✅
Spotify    ：需要 client credentials ✅（二选一）：
    · 环境变量  SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET
    · 或同目录 .env 文件（同 .env.example 的键名 ✅）
多地区：--country 给多个地区，Apple 与 Spotify 都会逐区搜索，再按「曲名+专辑」合并地区标记 ✅
ISRC  ：Spotify 结果自带 external_ids.isrc，会一并输出 ✅（Apple 接口不提供 ISRC）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
import base64
from dataclasses import dataclass, field, asdict

ITUNES_SEARCH = "https://itunes.apple.com/search"
SPOTIFY_TOKEN = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH = "https://api.spotify.com/v1/search"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MusicMetadataSearch/1.0"
DEFAULT_COUNTRIES = ["TW", "HK", "JP", "KR", "US", "CA"]


# ---------------------------------------------------------------- 数据模型
@dataclass
class Track:
    source: str = ""          # apple / spotify ✅
    name: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    track_id: str = ""        # Apple Music ID 或 Spotify Track ID ✅
    isrc: str = ""
    genre: str = ""
    release_date: str = ""
    artwork: str = ""
    url: str = ""
    countries: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        # 跨区合并：不同地区艺人名可能不同（周杰伦 / Jay Chou ✅）→ 用「曲名 + 专辑」✅
        return f"{self.source}|{self.name.strip().lower()}|{self.album.strip().lower()}"

    @property
    def duration(self) -> str:
        if not self.duration_ms:
            return "-"
        s = round(self.duration_ms / 1000)
        return f"{s // 60}:{s % 60:02d}"


def _get_json(url: str, timeout: int = 30, headers: dict | None = None) -> dict:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


# ---------------------------------------------------------------- Apple Music
def apple_search(term: str, country: str, limit: int) -> list[Track]:
    params = urllib.parse.urlencode(
        {"term": term, "media": "music", "entity": "song", "limit": limit, "country": country}
    )
    payload = _get_json(f"{ITUNES_SEARCH}?{params}")
    out: list[Track] = []
    for r in payload.get("results", []):
        if r.get("kind") and r.get("kind") != "song":
            continue
        out.append(
            Track(
                source="apple",
                name=r.get("trackName") or "",
                artist=r.get("artistName") or "",
                album=r.get("collectionName") or "",
                duration_ms=int(r.get("trackTimeMillis") or 0),
                track_id=str(r.get("trackId") or ""),
                genre=r.get("primaryGenreName") or "",
                release_date=(r.get("releaseDate") or "")[:10],
                artwork=r.get("artworkUrl100") or "",
                url=r.get("trackViewUrl") or "",
                countries=[country],
            )
        )
    return out


# ---------------------------------------------------------------- Spotify
def _read_env_file() -> dict:
    """从同目录 .env 读键值（不覆盖已存在的环境变量 ✅）。"""
    env: dict[str, str] = {}
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, ".env")
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


DEEZER_SEARCH = "https://api.deezer.com/search"


def deezer_search(term: str, market: str, limit: int) -> list[Track]:
    """Deezer 公开接口 ✅（无需密钥 ✅，返回 ISRC ✅）。"""
    params = urllib.parse.urlencode({"q": term, "limit": limit})
    data = _get_json(f"{DEEZER_SEARCH}?{params}")
    out: list[Track] = []
    for r in data.get("data", [])[:limit]:
        album = r.get("album") or {}
        artist = r.get("artist") or {}
        out.append(Track(
            source="deezer",
            name=r.get("title") or "",
            artist=artist.get("name") or "",
            album=album.get("title") or "",
            duration_ms=int(r.get("duration") or 0) * 1000,
            track_id=str(r.get("id") or ""),
            isrc=r.get("isrc") or "",
            artwork=album.get("cover_medium") or "",
            url=r.get("link") or "",
            countries=[market],
        ))
    return out

def spotify_token() -> str | None:
    env = _read_env_file()
    cid = os.environ.get("SPOTIFY_CLIENT_ID") or env.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or env.get("SPOTIFY_CLIENT_SECRET")
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


def spotify_search(term: str, market: str, limit: int, token: str) -> list[Track]:
    params = urllib.parse.urlencode({"q": term, "type": "track", "market": market, "limit": limit})
    payload = _get_json(f"{SPOTIFY_SEARCH}?{params}", headers={"Authorization": f"Bearer {token}"})
    out: list[Track] = []
    for r in (payload.get("tracks") or {}).get("items") or []:
        artists = ", ".join(a.get("name", "") for a in r.get("artists") or [])
        album = (r.get("album") or {}).get("name") or ""
        images = (r.get("album") or {}).get("images") or []
        ext = r.get("external_ids") or {}
        out.append(
            Track(
                source="spotify",
                name=r.get("name") or "",
                artist=artists,
                album=album,
                duration_ms=int(r.get("duration_ms") or 0),
                track_id=r.get("id") or "",
                isrc=(ext.get("isrc") or ""),          # ✅ ISRC 直接带上
                release_date=((r.get("album") or {}).get("release_date") or "")[:10],
                artwork=(images[0].get("url") if images else "") or "",
                url=((r.get("external_urls") or {}).get("spotify")) or "",
                countries=[market],
            )
        )
    return out


# ---------------------------------------------------------------- 多地区合并
def search_multi(term: str, sources: list[str], countries: list[str], limit: int) -> tuple[list[Track], list[str]]:
    merged: dict[str, Track] = {}
    order: list[str] = []
    warnings: list[str] = []
    token: str | None = None

    if "spotify" in sources:
        try:
            token = spotify_token()
        except Exception as exc:
            warnings.append(f"Spotify 取 token 失败：{exc}")
            token = None
        if token is None:
            warnings.append("Spotify 未配置（缺 SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET）→ 跳过")
            sources = [s for s in sources if s != "spotify"]

    for country in countries:
        for source in sources:
            try:
                if source == "apple":
                    found = apple_search(term, country, limit)
                elif source == "deezer":
                    found = deezer_search(term, country, limit) if country == countries[0] else []
                elif source == "spotify" and token:
                    found = spotify_search(term, country, limit, token)
                else:
                    continue
            except Exception as exc:
                warnings.append(f"{source}/{country} 失败：{exc}")
                continue

            for t in found:
                k = t.key
                if k.endswith("|"):
                    continue
                if k in merged:
                    if country not in merged[k].countries:
                        merged[k].countries.append(country)
                    if not merged[k].isrc and t.isrc:
                        merged[k].isrc = t.isrc
                else:
                    merged[k] = t
                    order.append(k)
    return [merged[k] for k in order], warnings


# ---------------------------------------------------------------- 输出
def print_table(tracks: list[Track]) -> None:
    if not tracks:
        print("（没有结果）")
        return
    wn = max(8, min(30, max(len(t.name) for t in tracks)))
    wa = max(6, min(20, max(len(t.artist) for t in tracks)))
    wb = max(5, min(24, max(len(t.album) for t in tracks)))
    header = (
        f"{'#':>3}  {'源':<7}  {'曲名':<{wn}}  {'艺人':<{wa}}  {'专辑':<{wb}}  "
        f"{'时长':>5}  {'地区':<16}  {'ID':<14}  {'ISRC':<14}"
    )
    print(header)
    print("-" * len(header))
    for i, t in enumerate(tracks, 1):
        print(
            f"{i:>3}  {t.source:<7}  {t.name[:wn]:<{wn}}  {t.artist[:wa]:<{wa}}  {t.album[:wb]:<{wb}}  "
            f"{t.duration:>5}  {','.join(t.countries):<16}  {t.track_id[:14]:<14}  {(t.isrc or '-'):<14}"
        )


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="音乐元数据搜索（Apple Music + Spotify，多地区）")
    parser.add_argument("term", nargs="?", help="关键词，例如：周杰伦 晴天")
    parser.add_argument("--artist", default=None, help="追加艺人名到关键词")
    parser.add_argument("--source", default="apple", help="数据源：apple / spotify / apple,spotify（默认 apple）")
    parser.add_argument(
        "--country",
        default=",".join(DEFAULT_COUNTRIES),
        help=f"地区列表（默认 {','.join(DEFAULT_COUNTRIES)}）",
    )
    parser.add_argument("--limit", type=int, default=15, help="每个地区每个源返回条数（默认 15）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--copy", action="store_true", help="复制第一条 ID")
    args = parser.parse_args()

    term = " ".join(x for x in [args.term, args.artist] if x).strip()
    if not term:
        parser.print_help()
        sys.exit(2)

    sources = [s.strip().lower() for s in args.source.split(",") if s.strip()]
    countries = [c.strip().upper() for c in args.country.split(",") if c.strip()]

    tracks, warnings = search_multi(term, sources, countries, args.limit)

    if args.json:
        print(json.dumps({"term": term, "sources": sources, "countries": countries,
                          "warnings": warnings, "results": [asdict(t) for t in tracks]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"关键词：{term}    源：{','.join(sources)}    地区：{','.join(countries)}    共 {len(tracks)} 条（已跨区合并）")
        for w in warnings:
            print(f"  [提示] {w}")
        print()
        print_table(tracks)

    if args.copy and tracks:
        try:
            import subprocess
            subprocess.run("clip", input=tracks[0].track_id.encode("utf-16le"), check=False, shell=True)
            print(f"\n已复制第一条 ID：{tracks[0].track_id}")
        except Exception as exc:
            print(f"\n复制失败：{exc}")


if __name__ == "__main__":
    main()
