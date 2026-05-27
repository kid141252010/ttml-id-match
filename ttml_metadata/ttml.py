from __future__ import annotations

import html
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from .models import AMLL_NS, TARGET_KEY_ORDER, AudioMetadata, TtmlLanguageNormalizationResult, TtmlUpdateResult, _MetaTag, _XmlAttribute
from .text_utils import _add_unique_value, _escape_xml_attr, _is_placeholder, _real_meta_value, _to_simplified_text, split_artists

def read_ttml_metadata(path: Path) -> AudioMetadata:
    text = path.read_text(encoding="utf-8")
    text, amll_prefix = _ensure_amll_namespace(text)
    metadata_start, metadata_end = _find_metadata_inner_bounds(text)
    metadata = text[metadata_start:metadata_end]
    values: dict[str, list[str]] = {}

    for tag in _iter_amll_meta_tags(metadata, amll_prefix):
        key = _xml_attr_value(tag, "key")
        if key not in {"musicName", "artists", "album", "appleMusicId", "isrc"}:
            continue
        value = _real_meta_value(_xml_attr_value(tag, "value"))
        if value:
            _add_unique_value(values, key, value)

    return AudioMetadata(
        title=values.get("musicName", [None])[0],
        artists=split_artists(values.get("artists", [])),
        album=values.get("album", [None])[0],
        isrc=values.get("isrc", [None])[0],
        catalog_id=values.get("appleMusicId", [None])[0],
    )


def update_ttml_metadata(
    path: Path,
    values: dict[str, list[str]],
    dry_run: bool,
    backup_paths: dict[Path, Path] | None = None,
) -> TtmlUpdateResult:
    text = path.read_text(encoding="utf-8")
    text, amll_prefix = _ensure_amll_namespace(text)
    metadata_start, metadata_end = _find_metadata_inner_bounds(text)
    metadata = text[metadata_start:metadata_end]
    result = TtmlUpdateResult()

    for key in TARGET_KEY_ORDER:
        proposed_values = [value for value in values.get(key, []) if value]
        if not proposed_values:
            continue
        metadata = _apply_meta_values(metadata, amll_prefix, key, proposed_values, result)

    if result.changed and not dry_run:
        backup_path = _ensure_backup(path, backup_paths)
        result.backup_path = backup_path
        output = text[:metadata_start] + metadata + text[metadata_end:]
        path.write_text(output, encoding="utf-8")

    return result


def normalize_ttml_language(
    path: Path,
    dry_run: bool,
    backup_paths: dict[Path, Path] | None = None,
) -> TtmlLanguageNormalizationResult:
    text = path.read_text(encoding="utf-8")
    root_match = _find_root_tt_tag(text)
    if not root_match:
        raise ValueError("missing <tt> root; refusing to normalize TTML language")

    root_tag = root_match.group(0)
    root_attrs = _parse_xml_attributes(root_tag, root_match.start())
    lang_attr = root_attrs.get("xml:lang")
    if not lang_attr or lang_attr.value != "zh-Hant":
        return TtmlLanguageNormalizationResult()

    result = TtmlLanguageNormalizationResult(language_changed=True)
    output = text[: lang_attr.value_start] + "zh-Hans" + text[lang_attr.value_end :]

    output, result.removed_translations = _remove_zh_hans_replacement_translations(output)
    output, result.removed_transliterations = _remove_pinyin_transliterations(output)
    output = _remove_empty_layer_containers(output, "translations")
    output = _remove_empty_layer_containers(output, "transliterations")

    converted = _convert_body_text_nodes_to_simplified(output)
    if converted != output:
        result.body_text_changed = True
        output = converted

    if result.changed:
        try:
            ET.fromstring(output)
        except ET.ParseError as exc:
            raise ValueError(f"normalized TTML is not valid XML: {exc}") from exc

    if result.changed and not dry_run:
        result.backup_path = _ensure_backup(path, backup_paths)
        path.write_text(output, encoding="utf-8")

    return result


def _find_metadata_inner_bounds(text: str) -> tuple[int, int]:
    open_match = re.search(r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?metadata)\b[^>]*>", text)
    if not open_match:
        raise ValueError("missing <metadata>; refusing to create TTML metadata nodes")

    tag_name = open_match.group("tag")
    close_match = re.search(rf"</{re.escape(tag_name)}\s*>", text[open_match.end() :])
    if not close_match:
        raise ValueError(f"missing </{tag_name}>; refusing to rewrite TTML")

    return open_match.end(), open_match.end() + close_match.start()


def _find_root_tt_tag(text: str) -> re.Match[str] | None:
    return re.search(r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?tt)\b[^>]*>", text, flags=re.DOTALL)


def _find_element_inner_bounds(text: str, local_name: str) -> tuple[int, int] | None:
    open_match = re.search(rf"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?{re.escape(local_name)})\b[^>]*>", text, flags=re.DOTALL)
    if not open_match:
        return None
    if open_match.group(0).rstrip().endswith("/>"):
        return None

    tag_name = open_match.group("tag")
    close_match = re.search(rf"</{re.escape(tag_name)}\s*>", text[open_match.end() :], flags=re.DOTALL)
    if not close_match:
        raise ValueError(f"missing </{tag_name}>; refusing to rewrite TTML")
    return open_match.end(), open_match.end() + close_match.start()


def _remove_zh_hans_replacement_translations(text: str) -> tuple[str, int]:
    pattern = re.compile(
        r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?translation)\b"
        r"(?=[^>]*\btype\s*=\s*(?P<type_quote>[\"'])replacement(?P=type_quote))"
        r"(?=[^>]*\bxml:lang\s*=\s*(?P<lang_quote>[\"'])zh-Hans(?P=lang_quote))"
        r"[^>]*(?:/>|>.*?</(?P=tag)\s*>)",
        flags=re.DOTALL,
    )
    return pattern.subn("", text)


def _remove_pinyin_transliterations(text: str) -> tuple[str, int]:
    pattern = re.compile(
        r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?transliteration)\b"
        r"(?=[^>]*\bxml:lang\s*=\s*(?P<lang_quote>[\"'])zh-Latn-pinyin(?P=lang_quote))"
        r"[^>]*(?:/>|>.*?</(?P=tag)\s*>)",
        flags=re.DOTALL,
    )
    return pattern.subn("", text)


def _remove_empty_layer_containers(text: str, local_name: str) -> str:
    pattern = re.compile(
        rf"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?{re.escape(local_name)})\b[^>]*>\s*</(?P=tag)\s*>",
        flags=re.DOTALL,
    )
    return pattern.sub("", text)


def _convert_body_text_nodes_to_simplified(text: str) -> str:
    bounds = _find_element_inner_bounds(text, "body")
    if not bounds:
        return text

    start, end = bounds
    body = text[start:end]
    converted = _convert_xml_text_nodes_to_simplified(
        body,
        skip_local_names={"translations", "translation", "transliterations", "transliteration"},
    )
    return text[:start] + converted + text[end:]


def _convert_xml_text_nodes_to_simplified(text: str, skip_local_names: set[str]) -> str:
    pieces = re.split(r"(<[^>]+>)", text)
    stack: list[str] = []
    output: list[str] = []

    for piece in pieces:
        if not piece:
            continue
        if piece.startswith("<"):
            _update_xml_stack(stack, piece)
            output.append(piece)
            continue
        if skip_local_names.isdisjoint(stack):
            output.append(_to_simplified_text(piece))
        else:
            output.append(piece)

    return "".join(output)


def _update_xml_stack(stack: list[str], tag_text: str) -> None:
    if tag_text.startswith(("<!--", "<?", "<!")):
        return

    close_match = re.match(r"</\s*(?P<name>[A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)", tag_text)
    if close_match:
        local_name = close_match.group("name").split(":")[-1]
        if stack and stack[-1] == local_name:
            stack.pop()
            return
        for index in range(len(stack) - 1, -1, -1):
            if stack[index] == local_name:
                del stack[index:]
                return
        return

    open_match = re.match(r"<\s*(?P<name>[A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)", tag_text)
    if not open_match or tag_text.rstrip().endswith("/>"):
        return
    stack.append(open_match.group("name").split(":")[-1])


def _find_amll_prefix(text: str) -> str:
    prefixes: list[str] = []
    for match in re.finditer(
        r"\bxmlns:(?P<prefix>[A-Za-z_][\w.-]*)\s*=\s*(?P<quote>[\"'])(?P<uri>.*?)\2",
        text,
        flags=re.DOTALL,
    ):
        if html.unescape(match.group("uri")) == AMLL_NS:
            prefixes.append(match.group("prefix"))

    if not prefixes:
        raise ValueError("missing AMLL namespace")
    if "amll" in prefixes:
        return "amll"
    return prefixes[0]


def _ensure_amll_namespace(text: str) -> tuple[str, str]:
    try:
        return text, _find_amll_prefix(text)
    except ValueError:
        pass

    root_match = re.search(r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?tt)\b[^>]*>", text, flags=re.DOTALL)
    if not root_match:
        raise ValueError("missing <tt> root; refusing to add AMLL namespace declaration")

    root_tag = root_match.group(0)
    amll_prefix_match = re.search(
        r"\bxmlns:amll\s*=\s*(?P<quote>[\"'])(?P<uri>.*?)\1",
        root_tag,
        flags=re.DOTALL,
    )
    if amll_prefix_match and html.unescape(amll_prefix_match.group("uri")) != AMLL_NS:
        raise ValueError("xmlns:amll already uses a different namespace; refusing to rewrite TTML")

    insert_at = root_match.end() - 1
    insertion = f' xmlns:amll="{AMLL_NS}"'
    return text[:insert_at] + insertion + text[insert_at:], "amll"


def _apply_meta_values(
    metadata: str,
    amll_prefix: str,
    key: str,
    proposed_values: list[str],
    result: TtmlUpdateResult,
) -> str:
    existing = [
        tag
        for tag in _iter_amll_meta_tags(metadata, amll_prefix)
        if _xml_attr_value(tag, "key") == key
    ]
    real_values = [
        _xml_attr_value(tag, "value") or ""
        for tag in existing
        if not _is_placeholder(_xml_attr_value(tag, "value"))
    ]
    placeholders = [tag for tag in existing if _is_placeholder(_xml_attr_value(tag, "value"))]
    unique_proposed_values: list[str] = []
    for value in proposed_values:
        if value not in unique_proposed_values:
            unique_proposed_values.append(value)

    if placeholders:
        replacements: list[tuple[int, int, str]] = []
        replacement_values = [value for value in unique_proposed_values if value not in real_values]
        for tag, value in zip(placeholders, replacement_values):
            value_attr = tag.attrs.get("value")
            if value_attr:
                replacements.append((value_attr.value_start, value_attr.value_end, _escape_xml_attr(value)))
            else:
                replacements.append((tag.start, tag.end, _make_meta_node(amll_prefix, key, value)))
        consumed_count = min(len(placeholders), len(replacement_values))
        for extra in placeholders[consumed_count:]:
            replacements.append((extra.start, extra.end, ""))
        metadata = _apply_text_replacements(metadata, replacements)

        remaining = replacement_values[consumed_count:]
        if replacement_values:
            result.replaced[key] = replacement_values
        if real_values:
            result.skipped[key] = real_values
        if remaining:
            metadata = _insert_meta_values(metadata, amll_prefix, key, remaining)
        return metadata

    missing_values = [value for value in unique_proposed_values if value not in real_values]
    if real_values:
        result.skipped[key] = real_values
    if not missing_values:
        return metadata

    metadata = _insert_meta_values(metadata, amll_prefix, key, missing_values)
    result.added[key] = missing_values
    return metadata


def _iter_amll_meta_tags(metadata: str, amll_prefix: str) -> Iterable[_MetaTag]:
    pattern = re.compile(
        rf"<{re.escape(amll_prefix)}:meta\b[^<>]*(?:/>|>\s*</{re.escape(amll_prefix)}:meta\s*>)",
        flags=re.DOTALL,
    )
    for match in pattern.finditer(metadata):
        yield _MetaTag(match.start(), match.end(), _parse_xml_attributes(match.group(0), match.start()))


def _parse_xml_attributes(tag_text: str, absolute_start: int) -> dict[str, _XmlAttribute]:
    attrs: dict[str, _XmlAttribute] = {}
    for match in re.finditer(
        r"(?P<name>[^\s=/>]+)\s*=\s*(?P<quote>[\"'])(?P<value>.*?)\2",
        tag_text,
        flags=re.DOTALL,
    ):
        attrs[match.group("name")] = _XmlAttribute(
            value=html.unescape(match.group("value")),
            value_start=absolute_start + match.start("value"),
            value_end=absolute_start + match.end("value"),
        )
    return attrs


def _xml_attr_value(tag: _MetaTag, name: str) -> str | None:
    attr = tag.attrs.get(name)
    return attr.value if attr else None


def _apply_text_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
    output = text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        output = output[:start] + replacement + output[end:]
    return output


def _insert_meta_values(metadata: str, amll_prefix: str, key: str, values: list[str]) -> str:
    insertion = "".join(_make_meta_node(amll_prefix, key, value) for value in values)
    index = _metadata_insert_index(metadata)
    return metadata[:index] + insertion + metadata[index:]


def _metadata_insert_index(metadata: str) -> int:
    match = re.search(r"<(?:[A-Za-z_][\w.-]*:)?iTunesMetadata\b", metadata)
    return match.start() if match else len(metadata)


def _make_meta_node(amll_prefix: str, key: str, value: str) -> str:
    return f'<{amll_prefix}:meta key="{_escape_xml_attr(key)}" value="{_escape_xml_attr(value)}"/>'


def _ensure_backup(path: Path, backup_paths: dict[Path, Path] | None = None) -> Path:
    key = _backup_map_key(path)
    if backup_paths is not None and key in backup_paths:
        return backup_paths[key]

    backup_path = _backup_path(path)
    shutil.copy2(path, backup_path)
    if backup_paths is not None:
        backup_paths[key] = backup_path
    return backup_path


def _backup_map_key(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _backup_path(path: Path) -> Path:
    candidate = path.with_suffix(path.suffix + ".bak")
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        numbered = path.with_suffix(path.suffix + f".bak{counter}")
        if not numbered.exists():
            return numbered
        counter += 1
