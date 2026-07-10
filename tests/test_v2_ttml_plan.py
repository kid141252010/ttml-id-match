from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ttml_metadata.v2.ttml_plan import TtmlInputChangedError, TtmlPlanner, TtmlWriter


INPUT_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:amll="http://www.example.com/ns/amll" xml:lang="zh-Hans">'
    '<head><metadata><iTunesMetadata/></metadata></head>'
    '<body><div><p>Song</p></div></body></tt>'
)


class TtmlPlannerTests(unittest.TestCase):
    def test_path_plan_is_pure_and_reports_exact_metadata_change(self) -> None:
        expected = (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:amll="http://www.example.com/ns/amll" xml:lang="zh-Hans">'
            '<head><metadata>'
            '<amll:meta key="musicName" value="Song"/>'
            '<iTunesMetadata/></metadata></head>'
            '<body><div><p>Song</p></div></body></tt>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(INPUT_TTML, encoding="utf-8")

            plan = TtmlPlanner().plan(path, {"musicName": ["Song"]})

            self.assertEqual(path.read_text(encoding="utf-8"), INPUT_TTML)
            self.assertEqual(plan.final_text, expected)
            self.assertEqual(
                plan.input_sha256,
                "8c46909f0d74bd2d55c5d6368e707437faf1fd3b92c0c03c128775f9e70256a7",
            )
            self.assertEqual(
                plan.output_sha256,
                "6cf54ec30a54bbfbbeec500394ad9d222c2bcafd888b1f26d42a6ea0c9f5ff83",
            )
            self.assertEqual(plan.metadata.added, {"musicName": ["Song"]})
            self.assertEqual(plan.metadata.replaced, {})
            self.assertEqual(plan.metadata.skipped, {})
            self.assertFalse(plan.language.changed)
            self.assertTrue(plan.changed)

    def test_raw_text_plan_combines_language_normalization_and_metadata(self) -> None:
        source = (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:amll="http://www.example.com/ns/amll" xml:lang="zh-Hant">'
            '<head><metadata><amll:meta key="musicName" value="*"/></metadata></head>'
            '<body><div><p>輪到我出場 我不會怯場</p>'
            '<translations>'
            '<translation type="replacement" xml:lang="zh-Hans"><p>替換文字</p></translation>'
            '<translation type="subtitle" xml:lang="en"><p>Keep English</p></translation>'
            '</translations>'
            '<transliterations>'
            '<transliteration xml:lang="zh-Latn-pinyin"><p>lun dao wo</p></transliteration>'
            '<transliteration xml:lang="ja-Latn"><p>keep latin</p></transliteration>'
            '</transliterations></div></body></tt>'
        )
        expected = (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:amll="http://www.example.com/ns/amll" xml:lang="zh-Hans">'
            '<head><metadata>'
            '<amll:meta key="musicName" value="浪费眼泪"/>'
            '</metadata></head>'
            '<body><div><p>轮到我出场 我不会怯场</p>'
            '<translations>'
            '<translation type="subtitle" xml:lang="en"><p>Keep English</p></translation>'
            '</translations>'
            '<transliterations>'
            '<transliteration xml:lang="ja-Latn"><p>keep latin</p></transliteration>'
            '</transliterations></div></body></tt>'
        )

        plan = TtmlPlanner().plan(source, {"musicName": ["浪费眼泪"]})

        self.assertEqual(plan.final_text, expected)
        self.assertEqual(plan.metadata.added, {})
        self.assertEqual(plan.metadata.replaced, {"musicName": ["浪费眼泪"]})
        self.assertEqual(plan.metadata.skipped, {})
        self.assertTrue(plan.language.language_changed)
        self.assertTrue(plan.language.body_text_changed)
        self.assertEqual(plan.language.removed_translations, 1)
        self.assertEqual(plan.language.removed_transliterations, 1)
        self.assertTrue(plan.language.changed)

    def test_existing_real_metadata_is_skipped_without_changing_output(self) -> None:
        source = INPUT_TTML.replace(
            "<iTunesMetadata/>",
            '<amll:meta key="musicName" value="Song"/><iTunesMetadata/>',
        )

        plan = TtmlPlanner().plan(source, {"musicName": ["Song"]})

        self.assertEqual(plan.final_text, source)
        self.assertEqual(plan.input_sha256, plan.output_sha256)
        self.assertEqual(plan.metadata.added, {})
        self.assertEqual(plan.metadata.replaced, {})
        self.assertEqual(plan.metadata.skipped, {"musicName": ["Song"]})
        self.assertFalse(plan.changed)


class TtmlWriterTests(unittest.TestCase):
    def test_write_atomically_applies_exact_plan_bytes_and_backs_up_original(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(INPUT_TTML, encoding="utf-8")
            plan = TtmlPlanner().plan(path, {"musicName": ["Song"]})

            result = TtmlWriter().write(path, plan)

            self.assertEqual(path.read_bytes(), plan.final_text.encode("utf-8"))
            self.assertEqual(result.path, path)
            self.assertEqual(result.output_sha256, plan.output_sha256)
            self.assertTrue(result.changed)
            self.assertEqual(result.backup_path, path.with_suffix(".ttml.bak"))
            self.assertEqual(result.backup_path.read_bytes(), INPUT_TTML.encode("utf-8"))

    def test_write_rejects_stale_input_before_creating_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(INPUT_TTML, encoding="utf-8")
            plan = TtmlPlanner().plan(path, {"musicName": ["Song"]})
            changed_after_preview = INPUT_TTML.replace(">Song</p>", ">Changed</p>")
            path.write_text(changed_after_preview, encoding="utf-8")

            with self.assertRaisesRegex(TtmlInputChangedError, "changed after preview"):
                TtmlWriter().write(path, plan)

            self.assertEqual(path.read_text(encoding="utf-8"), changed_after_preview)
            self.assertFalse(path.with_suffix(".ttml.bak").exists())

    def test_write_uses_numbered_backup_when_default_backup_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(INPUT_TTML, encoding="utf-8")
            default_backup = path.with_suffix(".ttml.bak")
            default_backup.write_text("older backup", encoding="utf-8")
            plan = TtmlPlanner().plan(path, {"musicName": ["Song"]})

            result = TtmlWriter().write(path, plan)

            self.assertEqual(default_backup.read_text(encoding="utf-8"), "older backup")
            self.assertEqual(result.backup_path, path.with_suffix(".ttml.bak1"))
            self.assertEqual(result.backup_path.read_bytes(), INPUT_TTML.encode("utf-8"))

    def test_shared_backup_map_reuses_original_backup_across_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(INPUT_TTML, encoding="utf-8")
            backup_paths: dict[Path, Path] = {}

            first_plan = TtmlPlanner().plan(path, {"musicName": ["Song"]})
            first = TtmlWriter().write(path, first_plan, backup_paths)
            second_plan = TtmlPlanner().plan(path, {"album": ["Album"]})
            second = TtmlWriter().write(path, second_plan, backup_paths)

            self.assertEqual(second.backup_path, first.backup_path)
            self.assertEqual(first.backup_path.read_bytes(), INPUT_TTML.encode("utf-8"))
            self.assertFalse(path.with_suffix(".ttml.bak1").exists())

    def test_unchanged_plan_does_not_write_or_create_backup(self) -> None:
        source = INPUT_TTML.replace(
            "<iTunesMetadata/>",
            '<amll:meta key="musicName" value="Song"/><iTunesMetadata/>',
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(source, encoding="utf-8")
            plan = TtmlPlanner().plan(path, {"musicName": ["Song"]})

            result = TtmlWriter().write(path, plan)

            self.assertFalse(result.changed)
            self.assertIsNone(result.backup_path)
            self.assertEqual(path.read_text(encoding="utf-8"), source)
            self.assertFalse(path.with_suffix(".ttml.bak").exists())


if __name__ == "__main__":
    unittest.main()
