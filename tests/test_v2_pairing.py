import unittest
from pathlib import Path

from ttml_metadata.v2.pairing import build_pairing_plan


class PairingPlanTests(unittest.TestCase):
    def test_nfkc_equivalent_ttml_names_are_blocked_with_unique_pair_ids(self):
        plan = build_pairing_plan(["Song.ttml", "Ｓｏｎｇ.ttml"])

        self.assertEqual(
            [issue.code for issue in plan.issues],
            ["duplicate_ttml_key", "duplicate_ttml_key"],
        )
        self.assertEqual(len({pair.pair_id for pair in plan.pairs}), 2)

    def test_case_and_exact_duplicate_ttml_names_still_get_unique_issue_ids(self):
        case_variants = build_pairing_plan(["Song.ttml", "song.ttml"])
        exact_duplicates = build_pairing_plan(["Song.ttml", "Song.ttml"])

        self.assertEqual(len({pair.pair_id for pair in case_variants.pairs}), 2)
        self.assertEqual(len({pair.pair_id for pair in exact_duplicates.pairs}), 2)

    def test_unique_same_stem_audio_is_paired(self) -> None:
        plan = build_pairing_plan([Path("Song.ttml"), Path("Song.mp3")])

        self.assertEqual(len(plan.pairs), 1)
        pair = plan.pairs[0]
        self.assertEqual(pair.status, "paired")
        self.assertEqual(pair.ttml_path, Path("Song.ttml"))
        self.assertEqual(pair.audio_path, Path("Song.mp3"))
        self.assertEqual(plan.issues, ())

    def test_stems_are_nfkc_trimmed_and_casefolded(self) -> None:
        plan = build_pairing_plan([Path("Ｓｏｎｇ .TTML"), Path("song.mp3")])

        self.assertEqual(len(plan.pairs), 1)
        self.assertEqual(plan.pairs[0].status, "paired")
        self.assertEqual(plan.pairs[0].audio_path, Path("song.mp3"))

    def test_ttml_without_same_stem_audio_is_kept_as_ttml_only(self) -> None:
        plan = build_pairing_plan([Path("Lyrics.ttml"), Path("Other.mp3")])

        self.assertEqual(len(plan.pairs), 1)
        pair = plan.pairs[0]
        self.assertEqual(pair.status, "ttml_only")
        self.assertEqual(pair.ttml_path, Path("Lyrics.ttml"))
        self.assertIsNone(pair.audio_path)

    def test_unique_flac_wins_when_multiple_same_stem_audio_files_exist(self) -> None:
        plan = build_pairing_plan(
            [Path("Song.ttml"), Path("Song.mp3"), Path("Song.FLAC"), Path("Song.wav")]
        )

        self.assertEqual(len(plan.pairs), 1)
        pair = plan.pairs[0]
        self.assertEqual(pair.status, "paired")
        self.assertEqual(pair.audio_path, Path("Song.FLAC"))
        self.assertEqual(pair.audio_candidates, ())
        self.assertEqual(plan.issues, ())

    def test_multiple_audio_without_unique_flac_is_ambiguous(self) -> None:
        plan = build_pairing_plan(
            [Path("Song.wav"), Path("Song.ttml"), Path("Song.mp3")]
        )

        self.assertEqual(len(plan.pairs), 1)
        pair = plan.pairs[0]
        self.assertEqual(pair.status, "ambiguous")
        self.assertIsNone(pair.audio_path)
        self.assertEqual(pair.audio_candidates, (Path("Song.mp3"), Path("Song.wav")))
        self.assertEqual(len(plan.issues), 1)
        issue = plan.issues[0]
        self.assertEqual(issue.code, "ambiguous_audio")
        self.assertEqual(issue.pair_id, pair.pair_id)
        self.assertEqual(issue.ttml_path, Path("Song.ttml"))
        self.assertEqual(issue.audio_candidates, pair.audio_candidates)

    def test_pair_order_and_hash_ids_are_stable(self) -> None:
        first = build_pairing_plan(
            [Path("B.mp3"), Path("B.ttml"), Path("A.mp3"), Path("A.ttml")]
        )
        second = build_pairing_plan(
            [Path("A.ttml"), Path("A.mp3"), Path("B.ttml"), Path("B.mp3")]
        )
        normalized_variant = build_pairing_plan(
            [Path("Ａ .TTML"), Path("a.mp3")]
        )

        self.assertEqual(
            [pair.ttml_path for pair in first.pairs],
            [Path("A.ttml"), Path("B.ttml")],
        )
        self.assertEqual(
            [pair.pair_id for pair in first.pairs],
            [pair.pair_id for pair in second.pairs],
        )
        self.assertRegex(first.pairs[0].pair_id, r"^pair-[0-9a-f]{16}$")
        self.assertNotEqual(first.pairs[0].pair_id, first.pairs[1].pair_id)
        self.assertEqual(normalized_variant.pairs[0].pair_id, first.pairs[0].pair_id)

    def test_plan_serializes_to_json_safe_data(self) -> None:
        plan = build_pairing_plan([Path("Song.ttml"), Path("Song.mp3")])

        data = plan.to_dict()

        self.assertEqual(
            data,
            {
                "pairs": [
                    {
                        "pair_id": plan.pairs[0].pair_id,
                        "status": "paired",
                        "ttml_path": "Song.ttml",
                        "audio_path": "Song.mp3",
                        "audio_candidates": [],
                    }
                ],
                "issues": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
