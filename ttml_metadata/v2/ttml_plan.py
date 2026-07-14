from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, MutableMapping, Sequence

from ..models import TARGET_KEY_ORDER, TtmlUpdateResult
from ..ttml import (
    _apply_meta_values,
    _convert_body_text_nodes_to_simplified,
    _ensure_amll_namespace,
    _find_metadata_inner_bounds,
    _find_root_tt_tag,
    _parse_xml_attributes,
    _remove_empty_layer_containers,
    _remove_pinyin_transliterations,
    _remove_zh_hans_replacement_translations,
)


@dataclass(frozen=True)
class MetadataChangeSummary:
    added: dict[str, list[str]] = field(default_factory=dict)
    replaced: dict[str, list[str]] = field(default_factory=dict)
    skipped: dict[str, list[str]] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.replaced)


@dataclass(frozen=True)
class LanguageNormalizationSummary:
    language_changed: bool = False
    body_text_changed: bool = False
    removed_translations: int = 0
    removed_transliterations: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.language_changed
            or self.body_text_changed
            or self.removed_translations
            or self.removed_transliterations
        )


@dataclass(frozen=True)
class ChangePlan:
    input_sha256: str
    output_sha256: str
    final_text: str
    metadata: MetadataChangeSummary
    language: LanguageNormalizationSummary

    @property
    def changed(self) -> bool:
        return self.input_sha256 != self.output_sha256


@dataclass(frozen=True)
class WriteResult:
    path: Path
    output_sha256: str
    changed: bool
    backup_path: Path | None = None


class TtmlInputChangedError(ValueError):
    pass


class TtmlPlanner:
    def plan(
        self,
        source: Path | str,
        metadata_values: Mapping[str, Sequence[str]] | None = None,
    ) -> ChangePlan:
        input_bytes, text = _read_source(source)
        _validate_xml(text, label="input TTML")
        output, language = _normalize_language(text)
        update = TtmlUpdateResult()
        normalized_values = {
            key: [str(value) for value in values if value]
            for key, values in (metadata_values or {}).items()
        }

        if any(normalized_values.values()):
            namespaced_text, amll_prefix = _ensure_amll_namespace(output)
            metadata_start, metadata_end = _find_metadata_inner_bounds(namespaced_text)
            metadata = namespaced_text[metadata_start:metadata_end]
            for key in TARGET_KEY_ORDER:
                proposed_values = normalized_values.get(key, [])
                if proposed_values:
                    metadata = _apply_meta_values(metadata, amll_prefix, key, proposed_values, update)
            if update.changed:
                output = namespaced_text[:metadata_start] + metadata + namespaced_text[metadata_end:]

        if output != text:
            _validate_xml(output, label="planned TTML")

        return ChangePlan(
            input_sha256=_sha256(input_bytes),
            output_sha256=_sha256(output.encode("utf-8")),
            final_text=output,
            metadata=MetadataChangeSummary(
                added=update.added,
                replaced=update.replaced,
                skipped=update.skipped,
            ),
            language=language,
        )


class TtmlWriter:
    def write(
        self,
        path: Path,
        plan: ChangePlan,
        backup_paths: MutableMapping[Path, Path] | None = None,
    ) -> WriteResult:
        current = path.read_bytes()
        current_sha256 = _sha256(current)
        if current_sha256 != plan.input_sha256:
            raise TtmlInputChangedError(
                "TTML input changed after preview: "
                f"expected {plan.input_sha256}, found {current_sha256}"
            )

        output = plan.final_text.encode("utf-8")
        if _sha256(output) != plan.output_sha256:
            raise ValueError("change plan output hash does not match final_text")
        if not plan.changed:
            return WriteResult(path=path, output_sha256=plan.output_sha256, changed=False)

        _validate_xml(plan.final_text, label="planned TTML")
        temp_path = _write_temp_bytes(path, output)
        try:
            backup_path = _ensure_backup(path, backup_paths)
            os.replace(temp_path, path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        return WriteResult(
            path=path,
            output_sha256=plan.output_sha256,
            changed=True,
            backup_path=backup_path,
        )


def _read_source(source: Path | str) -> tuple[bytes, str]:
    if isinstance(source, Path):
        data = source.read_bytes()
        return data, data.decode("utf-8")
    data = source.encode("utf-8")
    return data, source


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_language(text: str) -> tuple[str, LanguageNormalizationSummary]:
    root_match = _find_root_tt_tag(text)
    if not root_match:
        raise ValueError("missing <tt> root; refusing to normalize TTML language")

    root_attrs = _parse_xml_attributes(root_match.group(0), root_match.start())
    lang_attr = root_attrs.get("xml:lang")
    if not lang_attr or lang_attr.value != "zh-Hant":
        return text, LanguageNormalizationSummary()

    output = text[: lang_attr.value_start] + "zh-Hans" + text[lang_attr.value_end :]
    output, removed_translations = _remove_zh_hans_replacement_translations(output)
    output, removed_transliterations = _remove_pinyin_transliterations(output)
    output = _remove_empty_layer_containers(output, "translations")
    output = _remove_empty_layer_containers(output, "transliterations")
    converted = _convert_body_text_nodes_to_simplified(output)

    return converted, LanguageNormalizationSummary(
        language_changed=True,
        body_text_changed=converted != output,
        removed_translations=removed_translations,
        removed_transliterations=removed_transliterations,
    )


def _validate_xml(text: str, *, label: str) -> None:
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"{label} is not valid XML: {exc}") from exc


def _write_temp_bytes(path: Path, data: bytes) -> Path:
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as temp_file:
        temp_file.write(data)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        return Path(temp_file.name)


def _ensure_backup(
    path: Path,
    backup_paths: MutableMapping[Path, Path] | None,
) -> Path:
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
