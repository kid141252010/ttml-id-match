from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from ttml_metadata.v2.application import MatchingApplication, PairSnapshot
from ttml_metadata.v2.domain import Candidate, MatchContext, MatchEvidence, Selection, SourceResult
from ttml_metadata.v2.engine import MatchingEngine, UnknownCandidateError
from ttml_metadata.v2.pairing import PairingPair


INPUT_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:amll="http://www.example.com/ns/amll" xml:lang="zh-Hans">'
    '<head><metadata>'
    '<amll:meta key="musicName" value="Song"/>'
    '<amll:meta key="artists" value="Artist"/>'
    '<iTunesMetadata/></metadata></head>'
    '<body><div><p>Song</p></div></body></tt>'
)


class RecordingSource:
    key = "catalog"
    dependencies = frozenset()

    def __init__(self) -> None:
        self.search_calls = 0
        self.metadata_calls = 0
        self.fail_if_called = False

    def search(self, context: MatchContext) -> SourceResult:
        self.search_calls += 1
        if self.fail_if_called:
            raise AssertionError("source search must not run after preview")
        candidate = Candidate(
            id="catalog-1",
            source=self.key,
            title=context.metadata.title,
            artists=tuple(context.metadata.artists),
            identifiers={"catalog_id": "catalog-1"},
            group="default",
            rank=1,
            recommended=True,
            evidence=(
                MatchEvidence(
                    field="title",
                    relation="exact",
                    expected="Song",
                    actual="Song",
                ),
            ),
        )
        return SourceResult(
            source=self.key,
            candidates=(candidate,),
            groups={"default": (candidate.id,)},
            recommended_ids=(candidate.id,),
        )

    def metadata_values(self, metadata, result, selected_ids):
        self.metadata_calls += 1
        if self.fail_if_called:
            raise AssertionError("source metadata adapter must not run after preview")
        return {"qqMusicId": list(selected_ids)}


class MatchingApplicationTests(unittest.TestCase):

    def test_preview_snapshot_round_trips_through_json_without_losing_domain_data(self) -> None:
        source = RecordingSource()
        application = MatchingApplication(MatchingEngine([source]))

        with tempfile.TemporaryDirectory() as tmp:
            ttml_path = Path(tmp) / "Song.ttml"
            ttml_path.write_text(INPUT_TTML, encoding="utf-8")
            pair = PairingPair(
                pair_id="pair-song",
                status="ttml_only",
                ttml_path=ttml_path,
            )

            snapshot = application.preview_pair(pair)
            snapshot_data = snapshot.to_dict()
            encoded = json.dumps(snapshot_data)
            restored = PairSnapshot.from_dict(json.loads(encoded))

        self.assertEqual(restored, snapshot)
        self.assertEqual(snapshot.ttml_filename, "Song.ttml")
        self.assertEqual(
            snapshot.ttml_sha256,
            "df5fe2a362c798fbd1f720b280c830e5aa3d80fd669872d4a633817ee5e9febf",
        )
        self.assertIsNone(snapshot.audio_filename)
        self.assertIsNone(snapshot.audio_sha256)
        self.assertEqual(snapshot.metadata.title, "Song")
        self.assertEqual(snapshot.metadata.artists, ["Artist"])
        self.assertIn(
            '<amll:meta key="qqMusicId" value="catalog-1"/>',
            snapshot_data["baseline_change_plan"]["final_text"],
        )
        self.assertEqual(tuple(snapshot.match_result.sources), ("catalog",))
        self.assertEqual(
            snapshot.match_result.sources["catalog"].candidates[0].evidence[0],
            MatchEvidence(field="title", relation="exact", expected="Song", actual="Song"),
        )

    def test_default_selection_plan_matches_preview_baseline_without_adapter(self) -> None:
        source = RecordingSource()
        application = MatchingApplication(MatchingEngine([source]))

        with tempfile.TemporaryDirectory() as tmp:
            ttml_path = Path(tmp) / "Song.ttml"
            ttml_path.write_text(INPUT_TTML, encoding="utf-8")
            pair = PairingPair("pair-song", "ttml_only", ttml_path)
            previewed = application.preview_pair(pair)
            snapshot = PairSnapshot.from_dict(json.loads(json.dumps(previewed.to_dict())))
            calls_after_preview = (source.search_calls, source.metadata_calls)
            source.fail_if_called = True

            plan = application.plan_selection(
                snapshot,
                Selection(pair_id="pair-song", sources={"catalog": ("catalog-1",)}),
                ttml_path,
            )
            unchanged_text = ttml_path.read_text(encoding="utf-8")

        self.assertEqual(calls_after_preview, (1, 1))
        self.assertEqual((source.search_calls, source.metadata_calls), calls_after_preview)
        self.assertEqual(snapshot.baseline_change_plan.input_sha256, plan.input_sha256)
        self.assertEqual(snapshot.baseline_change_plan.output_sha256, plan.output_sha256)
        self.assertEqual(snapshot.baseline_change_plan.final_text, plan.final_text)
        self.assertEqual(snapshot.baseline_change_plan.changed, plan.changed)
        self.assertEqual(snapshot.baseline_change_plan.metadata, plan.metadata)
        self.assertEqual(snapshot.baseline_change_plan.normalization, plan.language)
        self.assertIn('<amll:meta key="qqMusicId" value="catalog-1"/>', plan.final_text)
        self.assertEqual(unchanged_text, INPUT_TTML)

    def test_plan_selection_rejects_candidate_missing_from_snapshot(self) -> None:
        source = RecordingSource()
        application = MatchingApplication(MatchingEngine([source]))

        with tempfile.TemporaryDirectory() as tmp:
            ttml_path = Path(tmp) / "Song.ttml"
            ttml_path.write_text(INPUT_TTML, encoding="utf-8")
            snapshot = application.preview_pair(
                PairingPair("pair-song", "ttml_only", ttml_path)
            )
            source.fail_if_called = True

            with self.assertRaisesRegex(
                UnknownCandidateError,
                "unknown candidates for catalog: missing",
            ):
                application.plan_selection(
                    snapshot,
                    Selection(pair_id="pair-song", sources={"catalog": ("missing",)}),
                    ttml_path,
                )

    def test_snapshot_serialization_cannot_mutate_baseline_summary(self) -> None:
        application = MatchingApplication(MatchingEngine([RecordingSource()]))

        with tempfile.TemporaryDirectory() as tmp:
            ttml_path = Path(tmp) / "Song.ttml"
            ttml_path.write_text(INPUT_TTML, encoding="utf-8")
            snapshot = application.preview_pair(
                PairingPair("pair-song", "ttml_only", ttml_path)
            )

            data = snapshot.to_dict()
            data["baseline_change_plan"]["metadata"]["added"]["qqMusicId"].append(
                "tampered"
            )

        self.assertEqual(
            snapshot.baseline_change_plan.metadata.added,
            {"qqMusicId": ["catalog-1"]},
        )


if __name__ == "__main__":
    unittest.main()
