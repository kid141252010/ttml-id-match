import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from fill_ttml_metadata import (
    AMLL_NS,
    TTML_NS,
    AudioMetadata,
    InMemoryAppleMusicClient,
    choose_apple_music_id,
    split_artists,
    update_ttml_metadata,
)


class ArtistSplittingTests(unittest.TestCase):
    def test_splits_apple_music_style_artist_string(self):
        self.assertEqual(
            split_artists(["A, B, C & D"]),
            ["A", "B", "C", "D"],
        )

    def test_preserves_single_band_name_with_ampersand(self):
        self.assertEqual(split_artists(["Florence & The Machine"]), ["Florence & The Machine"])


class AppleMusicMatchingTests(unittest.TestCase):
    def test_rejects_invalid_catalog_id_and_matches_album_track(self):
        metadata = AudioMetadata(
            title="Disease (Apple Music Live)",
            artists=["Lady Gaga"],
            album="Apple Music Live: MAYHEM Requiem",
            isrc="USUM72603828",
            catalog_id="1",
            playlist_id="6768201775",
            track_number=2,
            disc_number=1,
            duration_seconds=231.125,
        )
        client = InMemoryAppleMusicClient(
            {
                ("cn", "6768201775"): [
                    {
                        "id": "6768201778",
                        "name": "Intro (Apple Music Live)",
                        "trackNumber": 1,
                        "discNumber": 1,
                        "durationInMillis": 50000,
                    },
                    {
                        "id": "6768201779",
                        "name": "Disease (Apple Music Live)",
                        "trackNumber": 2,
                        "discNumber": 1,
                        "durationInMillis": 231125,
                    },
                ]
            }
        )

        result = choose_apple_music_id(metadata, client, ["cn"], False)

        self.assertEqual(result.value, "6768201779")
        self.assertEqual(result.source, "album:cn:track")


class TtmlUpdateTests(unittest.TestCase):
    def test_inserts_amll_meta_after_agents_before_itunes_metadata(self):
        xml = (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:itunes="http://music.apple.com/lyric-ttml-internal" '
            'xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
            'itunes:timing="Word" xml:lang="en">'
            "<head><metadata>"
            '<ttm:agent type="person" xml:id="v1"/>'
            '<iTunesMetadata xmlns="http://music.apple.com/lyric-ttml-internal"/>'
            "</metadata></head><body/></tt>"
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(xml, encoding="utf-8")

            result = update_ttml_metadata(
                path,
                {
                    "musicName": ["Disease (Apple Music Live)"],
                    "artists": ["Lady Gaga"],
                    "album": ["Apple Music Live: MAYHEM Requiem"],
                    "appleMusicId": ["6768201779"],
                    "isrc": ["USUM72603828"],
                },
                dry_run=False,
            )

            self.assertEqual(result.added["appleMusicId"], ["6768201779"])
            self.assertTrue(path.with_suffix(".ttml.bak").exists())

            root = ET.parse(path).getroot()
            metadata = root.find(f"{{{TTML_NS}}}head/{{{TTML_NS}}}metadata")
            self.assertIsNotNone(metadata)
            tags = [child.tag for child in list(metadata)]
            self.assertEqual(tags[0], "{http://www.w3.org/ns/ttml#metadata}agent")
            self.assertEqual(tags[1], f"{{{AMLL_NS}}}meta")
            self.assertTrue(tags[-1].endswith("iTunesMetadata"))

            amll_values = {
                child.attrib["key"]: child.attrib["value"]
                for child in metadata
                if child.tag == f"{{{AMLL_NS}}}meta"
            }
            self.assertEqual(amll_values["appleMusicId"], "6768201779")

    def test_preserves_existing_real_values_but_replaces_placeholders(self):
        xml = (
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:amll="http://www.example.com/ns/amll">'
            "<head><metadata>"
            '<amll:meta key="musicName" value="Existing"/>'
            '<amll:meta key="appleMusicId" value="*"/>'
            "</metadata></head><body/></tt>"
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.ttml"
            path.write_text(xml, encoding="utf-8")

            result = update_ttml_metadata(
                path,
                {
                    "musicName": ["New"],
                    "appleMusicId": ["1234567890"],
                },
                dry_run=False,
            )

            self.assertEqual(result.skipped["musicName"], ["Existing"])
            self.assertEqual(result.replaced["appleMusicId"], ["1234567890"])

            root = ET.parse(path).getroot()
            metadata = root.find(f"{{{TTML_NS}}}head/{{{TTML_NS}}}metadata")
            metas = [
                (child.attrib["key"], child.attrib["value"])
                for child in metadata
                if child.tag == f"{{{AMLL_NS}}}meta"
            ]
            self.assertIn(("musicName", "Existing"), metas)
            self.assertIn(("appleMusicId", "1234567890"), metas)
            self.assertNotIn(("musicName", "New"), metas)


if __name__ == "__main__":
    unittest.main()
