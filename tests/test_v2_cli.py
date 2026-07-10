import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from ttml_metadata.cli import main
from ttml_metadata.v2.application import MatchingApplication
from ttml_metadata.v2.engine import MatchingEngine
from ttml_metadata.v2.pairing import stable_pair_id


HANT_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:amll="http://www.example.com/ns/amll" xml:lang="zh-Hant">'
    '<head><metadata><amll:meta key="musicName" value="浪費眼淚"/></metadata></head>'
    '<body><div><p>浪費眼淚</p></div></body></tt>'
)


class V2CliTests(unittest.TestCase):
    def test_dry_run_and_apply_share_the_same_v2_planner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Song.ttml"
            path.write_text(HANT_TTML, encoding="utf-8")
            application = MatchingApplication(MatchingEngine([]))
            preview_output = StringIO()

            preview_exit = main(
                [tmp, "--dry-run"],
                application=application,
                stdout=preview_output,
            )

            self.assertEqual(preview_exit, 0)
            self.assertIn("[previewed] Song.ttml", preview_output.getvalue())
            self.assertEqual(path.read_text(encoding="utf-8"), HANT_TTML)

            apply_exit = main([tmp], application=application, stdout=StringIO())

            self.assertEqual(apply_exit, 0)
            self.assertIn('xml:lang="zh-Hans"', path.read_text(encoding="utf-8"))
            self.assertTrue(path.with_suffix(".ttml.bak").is_file())

    def test_json_output_uses_stable_pair_ids_and_change_plan_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Song.ttml").write_text(HANT_TTML, encoding="utf-8")
            output = StringIO()

            exit_code = main(
                [tmp, "--dry-run", "--json"],
                application=MatchingApplication(MatchingEngine([])),
                stdout=output,
            )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["pairs"][0]["pair_id"].startswith("pair-"))
            self.assertNotEqual(
                payload["pairs"][0]["change_plan"]["input_sha256"],
                payload["pairs"][0]["change_plan"]["output_sha256"],
            )

    def test_ambiguous_pairing_stops_before_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Song.ttml").write_text(HANT_TTML, encoding="utf-8")
            (root / "Song.mp3").write_bytes(b"one")
            (root / "song.wav").write_bytes(b"two")
            errors = StringIO()

            exit_code = main(
                [tmp, "--dry-run"],
                application=MatchingApplication(MatchingEngine([])),
                stderr=errors,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("ambiguous audio candidates", errors.getvalue())

    def test_batch_preview_keeps_valid_pairs_when_one_ttml_cannot_be_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Good.ttml").write_text(HANT_TTML, encoding="utf-8")
            (root / "Bad.ttml").write_text("not xml", encoding="utf-8")
            errors = StringIO()
            output = StringIO()

            exit_code = main(
                [tmp, "--dry-run"],
                application=MatchingApplication(MatchingEngine([])),
                stdout=output,
                stderr=errors,
            )

            self.assertEqual(exit_code, 1)
            self.assertIn("[previewed] Good.ttml", output.getvalue())
            self.assertIn("[error] Bad.ttml", errors.getvalue())

    def test_selection_file_must_cover_every_previewed_pair_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ttml_path = root / "Song.ttml"
            ttml_path.write_text(HANT_TTML, encoding="utf-8")
            selection_path = root / "selections.json"
            selection_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            {"pair_id": "pair-typo", "sources": {}}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            errors = StringIO()

            exit_code = main(
                [tmp, "--selection-file", str(selection_path)],
                application=MatchingApplication(MatchingEngine([])),
                stderr=errors,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("missing", errors.getvalue())
            self.assertIn("extra", errors.getvalue())
            self.assertEqual(ttml_path.read_text(encoding="utf-8"), HANT_TTML)

    def test_selection_file_rejects_non_array_source_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ttml_path = root / "Song.ttml"
            ttml_path.write_text(HANT_TTML, encoding="utf-8")
            selection_path = root / "selections.json"
            selection_path.write_text(
                json.dumps(
                    {
                        "selections": [
                            {
                                "pair_id": stable_pair_id(ttml_path),
                                "sources": {"qq_music": "qq-1"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            errors = StringIO()

            exit_code = main(
                [tmp, "--selection-file", str(selection_path)],
                application=MatchingApplication(MatchingEngine([])),
                stderr=errors,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("candidate id array", errors.getvalue())
            self.assertEqual(ttml_path.read_text(encoding="utf-8"), HANT_TTML)


if __name__ == "__main__":
    unittest.main()
