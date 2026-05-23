import tempfile
import unittest
from pathlib import Path

from fill_ttml_metadata import update_ttml_metadata


REFERENCE_STYLE_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
    'xmlns:tts="http://www.w3.org/ns/ttml#styling" '
    'xmlns:amll="http://www.example.com/ns/amll" '
    'xmlns:itunes="http://itunes.apple.com/lyric-ttml-extensions" '
    'xml:lang="zh-Hans" itunes:timing="Word">'
    '<head><metadata>'
    '<ttm:agent type="person" xml:id="v1"/>'
    '<amll:meta key="ttmlAuthorGithubLogin" value="kid141252010"/>'
    '<iTunesMetadata><songwriters><songwriter>A</songwriter></songwriters></iTunesMetadata>'
    '</metadata></head>'
    '<body dur="01:00.000"><div><p begin="00:00.000" end="00:01.000">x</p></div></body>'
    '</tt>'
)


class TtmlMetadataWriterTests(unittest.TestCase):
    def write_ttml(self, text: str, directory: Path) -> Path:
        path = directory / "song.ttml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_adds_meta_before_itunes_metadata_without_rewriting_outer_ttml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(REFERENCE_STYLE_TTML, Path(tmp))
            before = path.read_text(encoding="utf-8")

            result = update_ttml_metadata(
                path,
                {
                    "musicName": ["Song"],
                    "artists": ["Artist"],
                    "album": ["Album"],
                    "appleMusicId": ["123456789"],
                    "isrc": ["TST000000001"],
                },
                dry_run=False,
            )

            after = path.read_text(encoding="utf-8")
            self.assertEqual(
                before.split("<metadata>", 1)[0],
                after.split("<metadata>", 1)[0],
            )
            self.assertEqual(
                before.rsplit("</metadata>", 1)[1],
                after.rsplit("</metadata>", 1)[1],
            )
            self.assertNotIn("ns0", after)
            self.assertNotIn("ns1", after)
            self.assertNotIn("<?xml", after)
            self.assertEqual(after.count("<head>"), 1)
            self.assertEqual(after.count("<metadata>"), 1)

            expected_insert = (
                '<amll:meta key="musicName" value="Song"/>'
                '<amll:meta key="artists" value="Artist"/>'
                '<amll:meta key="album" value="Album"/>'
                '<amll:meta key="appleMusicId" value="123456789"/>'
                '<amll:meta key="isrc" value="TST000000001"/>'
                "<iTunesMetadata>"
            )
            self.assertIn(expected_insert, after)
            self.assertEqual(result.added["musicName"], ["Song"])
            self.assertIsNotNone(result.backup_path)
            self.assertTrue(result.backup_path.exists())

    def test_replaces_placeholder_values_in_place_and_removes_extra_placeholders(self) -> None:
        text = REFERENCE_STYLE_TTML.replace(
            "<iTunesMetadata>",
            '<amll:meta key="musicName" value="*"/>'
            '<amll:meta key="musicName" value=""/>'
            "<iTunesMetadata>",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            result = update_ttml_metadata(
                path,
                {"musicName": ["Real Song"]},
                dry_run=False,
            )

            after = path.read_text(encoding="utf-8")
            self.assertIn('<amll:meta key="musicName" value="Real Song"/>', after)
            self.assertNotIn('<amll:meta key="musicName" value="*"/>', after)
            self.assertNotIn('<amll:meta key="musicName" value=""/>', after)
            self.assertEqual(after.count('key="musicName"'), 1)
            self.assertEqual(result.replaced["musicName"], ["Real Song"])

    def test_existing_real_value_is_skipped_without_rewriting_file(self) -> None:
        text = REFERENCE_STYLE_TTML.replace(
            "<iTunesMetadata>",
            '<amll:meta key="musicName" value="Existing"/>'
            "<iTunesMetadata>",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            result = update_ttml_metadata(
                path,
                {"musicName": ["New"]},
                dry_run=False,
            )

            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertEqual(result.skipped["musicName"], ["Existing"])
            self.assertIsNone(result.backup_path)

    def test_missing_metadata_raises_without_creating_nodes(self) -> None:
        text = (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:amll="http://www.example.com/ns/amll"><body/></tt>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            with self.assertRaisesRegex(ValueError, "missing <metadata>"):
                update_ttml_metadata(path, {"musicName": ["Song"]}, dry_run=False)

            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_missing_amll_namespace_adds_namespace_to_root_tt(self) -> None:
        text = (
            '<tt xmlns="http://www.w3.org/ns/ttml">'
            "<head><metadata></metadata></head><body/></tt>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            result = update_ttml_metadata(path, {"musicName": ["Song"]}, dry_run=False)

            after = path.read_text(encoding="utf-8")
            self.assertIn(
                '<tt xmlns="http://www.w3.org/ns/ttml" '
                'xmlns:amll="http://www.example.com/ns/amll">',
                after,
            )
            self.assertIn('<amll:meta key="musicName" value="Song"/>', after)
            self.assertEqual(after.count("xmlns:amll="), 1)
            self.assertEqual(result.added["musicName"], ["Song"])

    def test_missing_amll_namespace_adds_namespace_to_multiline_root_tt(self) -> None:
        text = (
            '<tt xmlns="http://www.w3.org/ns/ttml"\n'
            '    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"\n'
            '    xml:lang="zh-Hans">\n'
            "<head><metadata></metadata></head><body/></tt>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ttml(text, Path(tmp))

            update_ttml_metadata(path, {"musicName": ["Song"]}, dry_run=False)

            after = path.read_text(encoding="utf-8")
            self.assertIn(
                'xml:lang="zh-Hans" xmlns:amll="http://www.example.com/ns/amll">',
                after,
            )
            self.assertIn('<amll:meta key="musicName" value="Song"/>', after)
            self.assertEqual(after.count("xmlns:amll="), 1)


if __name__ == "__main__":
    unittest.main()
