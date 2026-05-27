from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .apple_music import AppleMusicClient, confirm_apple_music_candidates
from .console import _safe_print
from .models import AUDIO_EXTENSIONS, PairMetadata, WorkItem
from .ncm_music import NCMusicClient, confirm_ncm_music_candidates
from .orchestration import _collect_ncm_music_metadata_for_pairs, _prepare_work_item, _print_language_normalization_result, _process_prepared_pair
from .qq_music import QQMusicClient, confirm_qq_music_candidates
from .spotify import SpotifyClient, confirm_spotify_candidates, load_spotify_credentials
from .ttml import normalize_ttml_language

def find_directory_work_items(directory: Path) -> tuple[list[WorkItem], list[str]]:
    ttml_files = sorted(directory.glob("*.ttml"))
    audio_by_stem: dict[str, list[Path]] = {}
    for child in directory.iterdir():
        if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS:
            audio_by_stem.setdefault(child.stem, []).append(child)

    work_items: list[WorkItem] = []
    warnings: list[str] = []
    for ttml in ttml_files:
        matches = sorted(audio_by_stem.get(ttml.stem, []), key=lambda path: (path.suffix.lower(), path.name.lower()))
        if len(matches) == 1:
            work_items.append(WorkItem(ttml, matches[0]))
        elif not matches:
            work_items.append(WorkItem(ttml))
        else:
            flac_matches = [match for match in matches if match.suffix.lower() == ".flac"]
            if len(flac_matches) == 1:
                work_items.append(WorkItem(ttml, flac_matches[0]))
            else:
                names = ", ".join(match.name for match in matches)
                warnings.append(f"{ttml.name}: multiple same-stem audio files found: {names}")
    return work_items, warnings


def find_directory_pairs(directory: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    work_items, warnings = find_directory_work_items(directory)
    pairs: list[tuple[Path, Path]] = []
    legacy_warnings = list(warnings)
    for item in work_items:
        if item.audio_path:
            pairs.append((item.audio_path, item.ttml_path))
        else:
            legacy_warnings.append(f"{item.ttml_path.name}: no same-stem audio file found")
    return pairs, legacy_warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill AMLL TTML metadata from paired audio files.")
    parser.add_argument("path", nargs="?", default=".", help="directory to batch-process")
    parser.add_argument("--audio", type=Path, help="single audio file")
    parser.add_argument("--ttml", type=Path, help="single TTML file")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing files")
    args = parser.parse_args(argv)

    if args.audio and not args.ttml:
        parser.error("--audio requires --ttml")

    if args.audio and args.ttml:
        work_items = [WorkItem(args.ttml, args.audio)]
        warnings: list[str] = []
    elif args.ttml:
        work_items = [WorkItem(args.ttml)]
        warnings: list[str] = []
    else:
        directory = Path(args.path)
        if not directory.is_dir():
            parser.error(f"{directory} is not a directory")
        work_items, warnings = find_directory_work_items(directory)

    for warning in warnings:
        _safe_print(f"[skip] {warning}")

    apple_music_client = AppleMusicClient()
    qq_music_client = QQMusicClient()
    ncm_music_client = NCMusicClient()
    spotify_credentials = load_spotify_credentials()
    spotify_client = SpotifyClient(spotify_credentials) if spotify_credentials.enabled else None
    failures = 0
    backup_paths: dict[Path, Path] = {}
    prepared_pairs: list[PairMetadata] = []
    for work_item in work_items:
        try:
            normalization = normalize_ttml_language(work_item.ttml_path, dry_run=args.dry_run, backup_paths=backup_paths)
            _print_language_normalization_result(work_item.ttml_path, normalization, dry_run=args.dry_run)
        except Exception as exc:
            failures += 1
            _safe_print(f"[error] {work_item.ttml_path.name}: {exc}", file=sys.stderr)
            continue

        try:
            prepared_pairs.append(
                _prepare_work_item(
                    work_item,
                    apple_music_client,
                    qq_music_client,
                    ncm_music_client,
                    spotify_client,
                )
            )
        except Exception as exc:
            failures += 1
            _safe_print(f"[error] {work_item.ttml_path.name}: {exc}", file=sys.stderr)

    confirm_apple_music_candidates(prepared_pairs, dry_run=args.dry_run)
    confirm_qq_music_candidates(prepared_pairs, dry_run=args.dry_run)
    _collect_ncm_music_metadata_for_pairs(prepared_pairs, ncm_music_client)
    confirm_ncm_music_candidates(prepared_pairs, dry_run=args.dry_run)
    confirm_spotify_candidates(prepared_pairs, dry_run=args.dry_run)

    for pair in prepared_pairs:
        try:
            _process_prepared_pair(pair, dry_run=args.dry_run, backup_paths=backup_paths)
        except Exception as exc:
            failures += 1
            _safe_print(f"[error] {pair.ttml_path.name}: {exc}", file=sys.stderr)

    return 1 if failures else 0
