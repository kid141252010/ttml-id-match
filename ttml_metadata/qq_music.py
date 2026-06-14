from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Callable

from .console import _safe_print, _color_text
from .models import AudioMetadata, PairMetadata, QQMusicCandidate, QQMusicClientProtocol, QQMusicSearchResult
from .network import proxy_url_for_source, urlopen_with_retry
from .text_utils import _add_unique_value, _nested_get, _same_raw_text, _stringify_tag_value, _text_match_score, split_artists

class QQMusicClient:
    def __init__(self, timeout: int = 20, proxy_url: str | None = None):
        self.timeout = timeout
        self.proxy_url = proxy_url if proxy_url is not None else proxy_url_for_source("qq_music")

    def search_songs(self, query: str) -> list[QQMusicCandidate]:
        request = self._build_search_request(query)
        with urlopen_with_retry(request, timeout=self.timeout, proxy_url=self.proxy_url) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        return _parse_qq_music_candidates(payload)

    def _build_search_request(self, query: str) -> urllib.request.Request:
        data = json.dumps(_qq_music_search_payload(query), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return urllib.request.Request(
            "http://u.y.qq.com/cgi-bin/musicu.fcg",
            data=data,
            headers={
                "Accept-Language": "zh-CN",
                "Accept": "application/json",
                "User-Agent": "QQMusic 14090508(android 12)",
                "Content-Type": "application/json",
            },
            method="POST",
        )


def collect_qq_music_metadata(
    metadata: AudioMetadata,
    client: QQMusicClientProtocol,
) -> QQMusicSearchResult:
    result = QQMusicSearchResult()
    if not metadata.title:
        result.errors.append("音频中未读取到歌名，跳过 QQ 音乐搜索")
        return result

    try:
        candidates = client.search_songs(metadata.title)
    except Exception as exc:
        result.errors.append(f"QQ 音乐搜索失败: {exc}")
        return result

    result.candidates = sorted(
        candidates,
        key=lambda candidate: (-_qq_music_candidate_score(metadata, candidate), candidate.source_index),
    )
    if not result.candidates:
        result.errors.append("QQ 音乐未找到带 songid 和 mid 的候选")
    return result


def confirm_qq_music_candidates(
    pairs: list[PairMetadata],
    dry_run: bool,
    input_func: Callable[[str], str] = input,
    print_func: Callable[..., None] | None = None,
) -> None:
    if print_func is None:
        print_func = _safe_print

    available = [pair for pair in pairs if pair.qq_music_metadata.candidates]
    for pair in available:
        pair.qq_music_metadata.selected = pair.qq_music_metadata.candidates[0]

    if dry_run or not available:
        return

    use_color = (print_func is _safe_print) and (input_func is input)

    print_func("")
    header_text = "QQ 音乐最佳候选："
    if use_color:
        header_text = _color_text(header_text, "header")
    print_func(header_text)

    for pair in available:
        best = pair.qq_music_metadata.candidates[0]
        cand_str = _format_qq_music_candidate(best)
        if use_color:
            cand_str = _color_text(cand_str, "highlight")
        print_func(f"  {pair.ttml_path.name}:")
        print_func(f"    - {cand_str}")

    while True:
        prompt_text = "Accept all QQ Music best candidates? Type Y to accept, N to choose alternatives: "
        if use_color:
            prompt_text = _color_text(prompt_text, "prompt")
        answer = input_func(prompt_text).strip()
        if answer.casefold() in {"y", "n"}:
            break
        print_func("Please type Y or N.")

    if answer.casefold() == "y":
        return

    for pair in available:
        options = pair.qq_music_metadata.candidates[:5]
        print_func("")
        cand_title = f"{pair.ttml_path.name} QQ 音乐候选："
        if use_color:
            cand_title = _color_text(cand_title, "info")
        print_func(cand_title)

        for index, candidate in enumerate(options, start=1):
            idx_str = f"  {index}."
            if use_color:
                idx_str = _color_text(idx_str, "info")
            print_func(f"{idx_str} {_format_qq_music_candidate(candidate)}")
        while True:
            sel_prompt = "Select 1-5, or press Enter to skip this song: "
            if use_color:
                sel_prompt = _color_text(sel_prompt, "prompt")
            answer = input_func(sel_prompt).strip()
            if not answer:
                pair.qq_music_metadata.selected = None
                break
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                pair.qq_music_metadata.selected = options[int(answer) - 1]
                break
            print_func("Invalid selection.")


def _merge_qq_music_metadata(
    values: dict[str, list[str]],
    metadata: AudioMetadata,
    candidate: QQMusicCandidate,
) -> None:
    _add_unique_value(values, "qqMusicId", candidate.song_id)
    _add_unique_value(values, "qqMusicId", candidate.mid)
    if candidate.title and not _same_raw_text(candidate.title, metadata.title):
        _add_unique_value(values, "musicName", candidate.title)
    if (
        candidate.subtitle
        and not _same_raw_text(candidate.subtitle, metadata.title)
        and not _same_raw_text(candidate.subtitle, candidate.title)
    ):
        _add_unique_value(values, "musicName", candidate.subtitle)
    for artist in candidate.artists:
        if not any(_same_raw_text(artist, existing) for existing in metadata.artists):
            _add_unique_value(values, "artists", artist)
    if candidate.album and not _same_raw_text(candidate.album, metadata.album):
        _add_unique_value(values, "album", candidate.album)


def _qq_music_search_payload(query: str) -> dict[str, Any]:
    return {
        "comm": {
            "ct": "11",
            "cv": "14090508",
            "v": "14090508",
            "tmeAppID": "qqmusic",
            "phonetype": "EBG-AN10",
            "deviceScore": "553.47",
            "devicelevel": "50",
            "newdevicelevel": "20",
            "rom": "HuaWei/EMOTION/EmotionUI_14.2.0",
            "os_ver": "12",
            "OpenUDID": "0",
            "OpenUDID2": "0",
            "QIMEI36": "0",
            "udid": "0",
            "chid": "0",
            "aid": "0",
            "oaid": "0",
            "taid": "0",
            "tid": "0",
            "wid": "0",
            "uid": "0",
            "sid": "0",
            "modeSwitch": "6",
            "teenMode": "0",
            "ui_mode": "2",
            "nettype": "1020",
            "v4ip": "",
        },
        "req": {
            "module": "music.search.SearchCgiService",
            "method": "DoSearchForQQMusicMobile",
            "param": {
                "search_type": 0,
                "query": query,
                "page_num": 1,
                "num_per_page": 30,
                "highlight": 0,
                "nqc_flag": 0,
                "multi_zhida": 0,
                "cat": 2,
                "grp": 1,
                "sin": 0,
                "sem": 0,
            },
        },
    }


def _parse_qq_music_candidates(payload: dict[str, Any]) -> list[QQMusicCandidate]:
    songs = _nested_get(payload, "req", "data", "body", "item_song")
    if not isinstance(songs, list):
        return []

    candidates: list[QQMusicCandidate] = []
    for index, song in enumerate(songs):
        if not isinstance(song, dict):
            continue
        song_id = _stringify_tag_value(song.get("id") or song.get("songid"))
        mid = _stringify_tag_value(song.get("mid") or song.get("songmid"))
        if not song_id or not mid:
            continue
        candidates.append(
            QQMusicCandidate(
                song_id=song_id,
                mid=mid,
                title=_stringify_tag_value(song.get("name") or song.get("title")),
                subtitle=_stringify_tag_value(song.get("subtitle")),
                artists=_qq_music_artists(song.get("singer")),
                album=_qq_music_album(song.get("album")),
                source_index=index,
            )
        )
    return candidates


def _qq_music_artists(value: Any) -> list[str]:
    if isinstance(value, dict):
        return split_artists([value.get("name")])
    if not isinstance(value, list):
        return split_artists([value]) if value else []
    artists: list[str] = []
    for item in value:
        name = _stringify_tag_value(item.get("name") if isinstance(item, dict) else item)
        for artist in split_artists([name]):
            if artist not in artists:
                artists.append(artist)
    return artists


def _qq_music_album(value: Any) -> str | None:
    if isinstance(value, dict):
        return _stringify_tag_value(value.get("name") or value.get("title"))
    return _stringify_tag_value(value)


def _qq_music_candidate_score(metadata: AudioMetadata, candidate: QQMusicCandidate) -> int:
    score = _text_match_score(metadata.title, candidate.title) * 100
    for artist in metadata.artists:
        score += max((_text_match_score(artist, candidate_artist) for candidate_artist in candidate.artists), default=0) * 60
    score += _text_match_score(metadata.album, candidate.album) * 30
    return score


def _format_qq_music_candidate(candidate: QQMusicCandidate) -> str:
    title = candidate.title or "-"
    subtitle = f" ({candidate.subtitle})" if candidate.subtitle else ""
    artists = "/".join(candidate.artists) or "-"
    album = candidate.album or "-"
    return f"{title}{subtitle} - {artists} - {album} [{candidate.song_id}, {candidate.mid}]"
