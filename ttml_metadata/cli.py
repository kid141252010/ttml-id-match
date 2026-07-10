from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from .v2.application import MatchingApplication, PairSnapshot
from .v2.domain import Selection
from .v2.pairing import PairingPair, build_pairing_plan, stable_pair_id
from .v2.runtime import build_default_engine
from .v2.transport import HttpxTransport
from .v2.ttml_plan import ChangePlan, TtmlWriter


def _positive_search_workers(value: str) -> int:
    try:
        workers = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--search-workers must be an integer") from exc
    if workers < 1:
        raise argparse.ArgumentTypeError("--search-workers must be at least 1")
    return workers






def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview and apply deterministic TTML ID Match v2 change plans."
    )
    parser.add_argument("path", nargs="?", default=".", help="directory to batch-process")
    parser.add_argument("--audio", type=Path, help="single audio file")
    parser.add_argument("--ttml", type=Path, help="single TTML file")
    parser.add_argument("--dry-run", action="store_true", help="preview without writing files")
    parser.add_argument("--json", action="store_true", help="emit machine-readable preview results")
    parser.add_argument(
        "--selection-file",
        type=Path,
        help="JSON file containing HTTP-v2-style selections",
    )
    parser.add_argument(
        "--search-workers",
        type=_positive_search_workers,
        default=3,
        metavar="N",
        help="global source-search concurrency budget (default: 3)",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    application: MatchingApplication | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr

    if args.audio and not args.ttml:
        parser.error("--audio requires --ttml")

    pairs, pairing_errors = _resolve_pairs(args, parser)
    if pairing_errors:
        for message in pairing_errors:
            print(f"[pairing-error] {message}", file=errors)
        return 2
    if not pairs:
        print("[pairing-error] no TTML files found", file=errors)
        return 2

    transport: HttpxTransport | None = None
    if application is None:
        transport = HttpxTransport()
        application = MatchingApplication(
            build_default_engine(
                max_workers=args.search_workers,
                transport=transport,
            )
        )

    try:
        batch = application.preview_pairs(pairs)
        try:
            selections = _load_selections(args.selection_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[selection-error] {exc}", file=errors)
            return 2
        if args.selection_file is not None:
            expected_pair_ids = {snapshot.pair_id for snapshot in batch.snapshots}
            selected_pair_ids = set(selections)
            if selected_pair_ids != expected_pair_ids:
                missing = sorted(expected_pair_ids - selected_pair_ids)
                extra = sorted(selected_pair_ids - expected_pair_ids)
                print(
                    f"[selection-error] selections must cover previewed pairs; "
                    f"missing={missing}, extra={extra}",
                    file=errors,
                )
                return 2
        pair_paths = {pair.pair_id: pair.ttml_path for pair in pairs}
        records: list[dict[str, object]] = []
        failures = len(batch.failures)
        for failure in batch.failures:
            records.append(
                {
                    "pair_id": failure.pair_id,
                    "ttml": failure.ttml_filename,
                    "status": "failed",
                    "error": failure.message,
                }
            )
            if not args.json:
                print(f"[error] {failure.ttml_filename}: {failure.message}", file=errors)

        writer = TtmlWriter()
        for snapshot in batch.snapshots:
            selection = selections.get(snapshot.pair_id, snapshot.default_selection)
            try:
                plan = application.plan_selection(
                    snapshot,
                    selection,
                    pair_paths[snapshot.pair_id],
                )
                status = "previewed"
                backup = None
                if not args.dry_run:
                    result = writer.write(pair_paths[snapshot.pair_id], plan)
                    status = "applied" if result.changed else "unchanged"
                    backup = result.backup_path.name if result.backup_path else None
                records.append(_result_record(snapshot, selection, plan, status, backup))
                if not args.json:
                    _print_result(snapshot, selection, plan, status, backup, output)
            except Exception as exc:
                failures += 1
                records.append(
                    {
                        "pair_id": snapshot.pair_id,
                        "ttml": snapshot.ttml_filename,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                if not args.json:
                    print(f"[error] {snapshot.ttml_filename}: {exc}", file=errors)

        if args.json:
            json.dump(
                {"version": 2, "dry_run": bool(args.dry_run), "pairs": records},
                output,
                ensure_ascii=False,
                indent=2,
            )
            print(file=output)
        return 1 if failures else 0
    finally:
        if transport is not None:
            transport.close()


def _resolve_pairs(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> tuple[list[PairingPair], list[str]]:
    if args.ttml:
        ttml_path = Path(args.ttml)
        if not ttml_path.is_file():
            parser.error(f"TTML file does not exist: {ttml_path}")
        audio_path = Path(args.audio) if args.audio else None
        if audio_path is not None and not audio_path.is_file():
            parser.error(f"audio file does not exist: {audio_path}")
        return [
            PairingPair(
                pair_id=stable_pair_id(ttml_path),
                status="paired" if audio_path else "ttml_only",
                ttml_path=ttml_path,
                audio_path=audio_path,
            )
        ], []

    directory = Path(args.path)
    if not directory.is_dir():
        parser.error(f"{directory} is not a directory")
    pairing = build_pairing_plan(path for path in directory.iterdir() if path.is_file())
    issues = [
        f"{issue.ttml_path.name}: ambiguous audio candidates: "
        + ", ".join(path.name for path in issue.audio_candidates)
        for issue in pairing.issues
    ]
    return list(pairing.pairs), issues


def _load_selections(path: Path | None) -> dict[str, Selection]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_selections = payload.get("selections") if isinstance(payload, dict) else payload
    if not isinstance(raw_selections, list):
        raise ValueError("selection file must contain a selections array")
    selections: dict[str, Selection] = {}
    for item in raw_selections:
        if not isinstance(item, dict) or not isinstance(item.get("sources"), dict):
            raise ValueError("selection file contains an invalid selection")
        pair_id = str(item.get("pair_id", ""))
        if not pair_id or pair_id in selections:
            raise ValueError("selection pair ids must be non-empty and unique")
        sources: dict[str, tuple[str, ...]] = {}
        for source, candidate_ids in item["sources"].items():
            if not isinstance(candidate_ids, list):
                raise ValueError(
                    f"selection source {source!r} must contain a candidate id array"
                )
            sources[str(source)] = tuple(
                str(candidate_id) for candidate_id in candidate_ids
            )
        selections[pair_id] = Selection(pair_id=pair_id, sources=sources)
    return selections


def _result_record(
    snapshot: PairSnapshot,
    selection: Selection,
    plan: ChangePlan,
    status: str,
    backup: str | None,
) -> dict[str, object]:
    return {
        "pair_id": snapshot.pair_id,
        "ttml": snapshot.ttml_filename,
        "status": status,
        "selection": {
            "pair_id": selection.pair_id,
            "sources": {key: list(ids) for key, ids in selection.sources.items()},
        },
        "sources": {
            key: {
                "candidate_count": len(result.candidates),
                "recommended_ids": list(result.recommended_ids),
                "warnings": list(result.warnings),
            }
            for key, result in snapshot.match_result.sources.items()
        },
        "change_plan": {
            "input_sha256": plan.input_sha256,
            "output_sha256": plan.output_sha256,
            "changed": plan.changed,
            "metadata": {
                "added": plan.metadata.added,
                "replaced": plan.metadata.replaced,
                "skipped": plan.metadata.skipped,
            },
            "normalization": {
                "language_changed": plan.language.language_changed,
                "body_text_changed": plan.language.body_text_changed,
                "removed_translations": plan.language.removed_translations,
                "removed_transliterations": plan.language.removed_transliterations,
            },
        },
        "backup": backup,
    }


def _print_result(
    snapshot: PairSnapshot,
    selection: Selection,
    plan: ChangePlan,
    status: str,
    backup: str | None,
    output: TextIO,
) -> None:
    print(f"[{status}] {snapshot.ttml_filename} ({snapshot.pair_id})", file=output)
    for source, result in snapshot.match_result.sources.items():
        selected = ", ".join(selection.sources.get(source, ())) or "-"
        print(
            f"  {source}: {len(result.candidates)} candidates; selected={selected}",
            file=output,
        )
        for warning in result.warnings:
            print(f"    warning: {warning}", file=output)
    print(
        f"  change-plan: changed={str(plan.changed).lower()} "
        f"{plan.input_sha256[:12]} -> {plan.output_sha256[:12]}",
        file=output,
    )
    if backup:
        print(f"  backup: {backup}", file=output)
