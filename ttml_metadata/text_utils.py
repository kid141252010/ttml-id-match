from __future__ import annotations

import html
import re
from typing import Any, Iterable

_OPENCC_T2S: Any | None = None


def _to_simplified_text(value: str) -> str:
    global _OPENCC_T2S
    if _OPENCC_T2S is None:
        from opencc import OpenCC

        _OPENCC_T2S = OpenCC("t2s")
    return _OPENCC_T2S.convert(value)

def split_artists(values: Iterable[Any]) -> list[str]:
    artists: list[str] = []
    for raw_value in values:
        value = str(raw_value).strip()
        if not value:
            continue
        pieces = _split_artist_value(value)
        for piece in pieces:
            if piece and piece not in artists:
                artists.append(piece)
    return artists


def _text_match_score(expected: Any, actual: Any) -> int:
    expected_text = _normalize_match_text(expected)
    actual_text = _normalize_match_text(actual)
    if not expected_text or not actual_text:
        return 0
    if expected_text == actual_text:
        return 2
    if expected_text in actual_text or actual_text in expected_text:
        return 1
    return 0


def _normalize_match_text(value: Any) -> str:
    if value is None:
        return ""
    normalized = _to_simplified_text(str(value)).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _same_raw_text(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left).strip() == str(right).strip()


def _same_identifier(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left).strip().casefold() == str(right).strip().casefold()


def _nested_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _stringify_tag_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, tuple):
        value = "/".join(str(part) for part in value if part)
    text = str(value).strip()
    return text or None


def _parse_number(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, tuple | list):
        if not value:
            return None
        value = value[0]
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _is_placeholder(value: str | None) -> bool:
    return value is None or value.strip() in {"", "*"}


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _id_from_url(url: str) -> str | None:
    match = re.search(r"/(\d+)(?:[/?#].*)?$", url)
    return match.group(1) if match else None


def _iso8601_duration_to_millis(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"P(?:T)?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _add_unique_value(values: dict[str, list[str]], key: str, value: str | None) -> None:
    if not value:
        return
    if value not in values.setdefault(key, []):
        values[key].append(value)


def _add_unique_list_value(values: list[str], value: str | None) -> None:
    if value and value not in values:
        values.append(value)


def _real_meta_value(value: str | None) -> str | None:
    if _is_placeholder(value):
        return None
    assert value is not None
    return value.strip()


def _escape_xml_attr(value: str) -> str:
    return html.escape(str(value), quote=True)


def _duration_close(seconds: float, millis: Any, tolerance_seconds: float = 2.0) -> bool:
    try:
        track_seconds = float(millis) / 1000
    except (TypeError, ValueError):
        return False
    return abs(track_seconds - seconds) <= tolerance_seconds


def _normalize_release_date(value: Any) -> str | None:
    text = _stringify_tag_value(value)
    if not text:
        return None
    match = re.search(r"(\d{4})(?:[-./](\d{1,2})(?:[-./](\d{1,2}))?)?", text)
    if not match:
        return None
    year, month, day = match.groups()
    if day and month:
        return f"{year}-{int(month):02d}-{int(day):02d}"
    if month:
        return f"{year}-{int(month):02d}"
    return year


def _release_date_matches(expected: Any, actual: Any, actual_precision: Any = None) -> bool:
    expected_date = _normalize_release_date(expected)
    actual_date = _normalize_release_date(actual)
    if not expected_date or not actual_date:
        return False

    expected_parts = expected_date.split("-")
    actual_parts = actual_date.split("-")
    precision = (_stringify_tag_value(actual_precision) or "").casefold()
    if len(expected_parts) == 3:
        if precision and precision != "day":
            return False
        return len(actual_parts) == 3 and expected_parts == actual_parts
    if len(expected_parts) == 2:
        if precision == "year":
            return False
        return len(actual_parts) >= 2 and expected_parts[:2] == actual_parts[:2]
    return expected_parts[0] == actual_parts[0]


def _release_date_distance(expected: Any, actual: Any) -> int:
    expected_date = _normalize_release_date(expected)
    actual_date = _normalize_release_date(actual)
    if not expected_date or not actual_date:
        return 10_000_000
    expected_parts = [int(part) for part in expected_date.split("-")]
    actual_parts = [int(part) for part in actual_date.split("-")]
    while len(expected_parts) < 3:
        expected_parts.append(1)
    while len(actual_parts) < 3:
        actual_parts.append(1)
    return abs(
        (expected_parts[0] * 372 + expected_parts[1] * 31 + expected_parts[2])
        - (actual_parts[0] * 372 + actual_parts[1] * 31 + actual_parts[2])
    )


def _normalize_title(value: Any) -> str:
    if value is None:
        return ""
    normalized = str(value).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.replace("’", "'").replace("`", "'")
    return normalized


def _split_artist_value(value: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"\s*(?:,|;|；|、|&|＆)\s*", value)
        if part.strip()
    ]


def _text_with_simplified_variants(value: Any) -> list[str]:
    variants: list[str] = []
    _add_text_with_simplified_variants(variants, value)
    return variants


def _add_text_with_simplified_variants(values: list[str], value: Any) -> None:
    text = _stringify_tag_value(value)
    if not text:
        return
    for variant in (text, _to_simplified_text(text)):
        if variant and variant not in values:
            values.append(variant)


def _instrumental_marker_conflicts(expected_title: Any, candidate_title: Any) -> bool:
    return not _has_instrumental_marker(expected_title) and _has_instrumental_marker(candidate_title)


def _instrumental_titles_match(expected_title: Any, candidate_title: Any) -> bool:
    if not (_has_instrumental_marker(expected_title) and _has_instrumental_marker(candidate_title)):
        return False
    return _text_match_score(
        _strip_instrumental_markers(expected_title),
        _strip_instrumental_markers(candidate_title),
    ) > 0


def _strip_instrumental_markers(value: Any) -> str:
    text = _normalize_match_text(value)
    for marker in _instrumental_markers():
        text = text.replace(marker.strip(), " ")
    text = re.sub(r"[-_()[\]{}]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has_instrumental_marker(value: Any) -> bool:
    text = _normalize_match_text(value)
    if not text:
        return False
    padded = f" {text} "
    return any(marker in padded for marker in _instrumental_markers())


def _instrumental_markers() -> list[str]:
    return [
        "instrumental",
        " inst",
        "inst.",
        "off vocal",
        "off-vocal",
        "karaoke",
        "伴奏",
        "纯音乐",
        "純音樂",
        "インスト",
        "カラオケ",
        "반주",
    ]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag
