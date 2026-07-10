import unittest

from ttml_metadata.models import (
    AudioMetadata,
    AppleMusicTrackCandidate,
    InMemoryAppleMusicClient,
    NCMusicCandidate,
    QQMusicCandidate,
    SpotifyTrackCandidate,
)
from ttml_metadata.v2.domain import MatchContext, SourceResult
from ttml_metadata.v2.sources import (
    AppleMusicSourceAdapter,
    NCMusicSourceAdapter,
    QQMusicSourceAdapter,
    SpotifySourceAdapter,
)


class FakeQQMusicClient:
    def search_songs(self, query):
        return [
            QQMusicCandidate(
                song_id="wrong",
                mid="wrong-mid",
                title="Other Song",
                artists=["Other Artist"],
                album="Other Album",
                source_index=0,
            ),
            QQMusicCandidate(
                song_id="exact",
                mid="exact-mid",
                title="Song",
                artists=["Artist"],
                album="Album",
                source_index=10,
            ),
        ]


class SourceAdapterTests(unittest.TestCase):
    def test_all_source_adapters_convert_provider_failures_to_warnings(self):
        metadata = AudioMetadata(title="Song", artists=["Artist"], album="Album")

        class FailingClient:
            def search_songs(self, *_args):
                raise OSError("provider unavailable")

            def search_tracks(self, *_args):
                raise OSError("provider unavailable")

            def search_artists(self, *_args):
                return []

            def fetch_artist_albums(self, *_args):
                return [], []

            def fetch_album_tracks(self, *_args):
                return []

        failing = FailingClient()
        cases = (
            (
                QQMusicSourceAdapter(failing),
                MatchContext(metadata=metadata, results={}),
            ),
            (
                NCMusicSourceAdapter(failing),
                MatchContext(
                    metadata=metadata,
                    results={"qq_music": SourceResult(source="qq_music")},
                ),
            ),
            (
                AppleMusicSourceAdapter(failing, storefront_workers=1),
                MatchContext(metadata=metadata, results={}),
            ),
            (
                SpotifySourceAdapter(failing),
                MatchContext(metadata=metadata, results={}),
            ),
        )

        for adapter, context in cases:
            with self.subTest(source=adapter.key):
                result = adapter.search(context)
                self.assertEqual(result.candidates, ())
                self.assertTrue(result.warnings)

    def test_all_source_adapters_share_the_public_contract_and_metadata_seam(self):
        metadata = AudioMetadata(title="Song", artists=["Artist"], album="Album")
        qq_adapter = QQMusicSourceAdapter(FakeQQMusicClient())
        qq = qq_adapter.search(MatchContext(metadata=metadata, results={}))

        class NCMClient:
            def search_songs(self, _context):
                return [NCMusicCandidate("ncm-1", "Song", artists=["Artist"], album="Album")]

        ncm_adapter = NCMusicSourceAdapter(NCMClient())
        ncm = ncm_adapter.search(MatchContext(metadata=metadata, results={"qq_music": qq}))
        apple_adapter = AppleMusicSourceAdapter(InMemoryAppleMusicClient(
            albums={},
            searches={"cn": [AppleMusicTrackCandidate("apple-1", "Song", ["Artist"], "Album", "cn")]},
        ))
        apple = apple_adapter.search(MatchContext(metadata=metadata, results={}))

        class SpotifyClient:
            def search_tracks(self, _metadata):
                return [SpotifyTrackCandidate("spotify-1", "Song", ["Artist"], "Album", "US")]

        spotify_adapter = SpotifySourceAdapter(SpotifyClient())
        spotify = spotify_adapter.search(MatchContext(metadata=metadata, results={}))

        for adapter, result in (
            (qq_adapter, qq),
            (ncm_adapter, ncm),
            (apple_adapter, apple),
            (spotify_adapter, spotify),
        ):
            with self.subTest(source=adapter.key):
                self.assertEqual(result.source, adapter.key)
                self.assertEqual(
                    [candidate.rank for candidate in result.candidates],
                    list(range(1, len(result.candidates) + 1)),
                )
                known = {candidate.id for candidate in result.candidates}
                self.assertLessEqual(set(result.recommended_ids), known)
                self.assertTrue(all(candidate.evidence for candidate in result.candidates))
                self.assertTrue(all(set(ids) <= known for ids in result.groups.values()))
                self.assertEqual(
                    adapter.sort_candidates(tuple(reversed(result.candidates))),
                    result.candidates,
                )
                self.assertEqual(
                    adapter.recommended_candidate_ids(result.candidates),
                    result.recommended_ids,
                )
                self.assertEqual(
                    adapter.match_evidence(result.candidates[0]),
                    result.candidates[0].evidence,
                )
                selected = result.recommended_ids or (result.candidates[0].id,)
                self.assertTrue(adapter.metadata_values(metadata, result, selected))

    def test_qq_adapter_exposes_rank_recommendation_and_evidence_from_matching_order(self):
        metadata = AudioMetadata(title="Song", artists=["Artist"], album="Album")
        adapter = QQMusicSourceAdapter(FakeQQMusicClient())

        result = adapter.search(MatchContext(metadata=metadata, results={}))

        self.assertEqual([candidate.id for candidate in result.candidates], ["exact", "wrong"])
        self.assertEqual([candidate.rank for candidate in result.candidates], [1, 2])
        self.assertTrue(result.candidates[0].recommended)
        self.assertFalse(result.candidates[1].recommended)
        self.assertIn(("title", "exact"), [(item.field, item.relation) for item in result.candidates[0].evidence])
        self.assertEqual(result.recommended_ids, ("exact",))

        values = adapter.metadata_values(metadata, result, ["exact"])
        self.assertEqual(values["qqMusicId"], ["exact", "exact-mid"])

    def test_ncm_adapter_uses_recommended_qq_candidate_as_search_context(self):
        metadata = AudioMetadata(title="Song", artists=["Artist"], album="Album")
        qq_result = QQMusicSourceAdapter(FakeQQMusicClient()).search(MatchContext(metadata=metadata, results={}))

        class RecordingNCMClient:
            def __init__(self):
                self.context = None

            def search_songs(self, context):
                self.context = context
                return [NCMusicCandidate(song_id="ncm-1", title="Song", artists=["Artist"], album="Album")]

        client = RecordingNCMClient()
        result = NCMusicSourceAdapter(client).search(
            MatchContext(metadata=metadata, results={"qq_music": qq_result})
        )

        self.assertIn("Song", client.context.titles)
        self.assertEqual(result.recommended_ids, ("ncm-1",))
        self.assertEqual(result.candidates[0].rank, 1)

    def test_grouped_sources_use_unique_candidate_ids_and_recommended_groups(self):
        metadata = AudioMetadata(
            title="Song",
            artists=["Artist"],
            album="Album",
            isrc="ISRC-1",
            duration_seconds=180.0,
            release_date="2026-07-10",
        )
        apple = AppleMusicSourceAdapter(
            InMemoryAppleMusicClient(
                albums={},
                searches={
                    "cn": [AppleMusicTrackCandidate(
                        "same", "Song", ["Artist"], "Album", "cn",
                        isrc="ISRC-1", duration_ms=180_000, release_date="2026-07-10",
                    )],
                    "us": [AppleMusicTrackCandidate("same", "Song", ["Artist"], "Album", "us")],
                },
            )
        ).search(MatchContext(metadata=metadata, results={}))

        class SpotifyClient:
            def search_tracks(self, _metadata):
                return [
                    SpotifyTrackCandidate("same", "Song", ["Artist"], "Album", "US", source_index=0),
                    SpotifyTrackCandidate("same", "Song", ["Artist"], "Album", "JP", source_index=1),
                ]

        spotify = SpotifySourceAdapter(SpotifyClient()).search(MatchContext(metadata=metadata, results={}))

        self.assertEqual(set(apple.groups), {"cn", "us"})
        self.assertEqual(len(set(candidate.id for candidate in apple.candidates)), 2)
        self.assertEqual(set(apple.recommended_ids), set(candidate.id for candidate in apple.candidates))
        self.assertEqual(set(spotify.groups), {"US", "JP"})
        self.assertEqual(len(set(candidate.id for candidate in spotify.candidates)), 2)
        evidence = {
            item.field: item.relation for item in apple.candidates[0].evidence
        }
        self.assertEqual(evidence["isrc"], "exact")
        self.assertEqual(evidence["duration"], "close")
        self.assertEqual(evidence["release_date"], "exact")


if __name__ == "__main__":
    unittest.main()
