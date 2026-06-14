import tempfile
import time
import unittest
from pathlib import Path

from ttml_metadata.models import (
    AppleMusicTrackCandidate,
    NCMusicCandidate,
    QQMusicCandidate,
    SpotifyTrackCandidate,
    WorkItem,
)
from ttml_metadata.search_scheduler import (
    BatchSearchCache,
    SearchClients,
    collect_ncm_for_pairs,
    prepare_work_items,
)


REFERENCE_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
    'xmlns:amll="http://www.example.com/ns/amll" '
    'xml:lang="zh-Hans">'
    '<head><metadata>'
    '<amll:meta key="musicName" value="Song"/>'
    '<amll:meta key="artists" value="Artist"/>'
    '<amll:meta key="album" value="Album"/>'
    '</metadata></head>'
    '<body><div><p begin="00:00.000" end="00:01.000">x</p></div></body>'
    '</tt>'
)


class CountingAppleMusicClient:
    def __init__(self, delay: float = 0):
        self.delay = delay
        self.search_calls: list[tuple[str, str | None]] = []

    def fetch_album_tracks(self, store: str, album_id: str):
        return []

    def search_songs(self, store: str, metadata):
        time.sleep(self.delay)
        self.search_calls.append((store, metadata.title))
        return [
            AppleMusicTrackCandidate(
                track_id=f"apple-{store}",
                title=metadata.title,
                artists=list(metadata.artists),
                album=metadata.album,
                storefront=store,
            )
        ]

    def search_artists(self, store: str, query: str):
        return []

    def fetch_artist_albums(self, store: str, artist_id: str):
        return [], []


class CountingQQMusicClient:
    def __init__(self, delay: float = 0):
        self.delay = delay
        self.calls: list[str] = []

    def search_songs(self, query: str):
        time.sleep(self.delay)
        self.calls.append(query)
        return [QQMusicCandidate(song_id="qq-song", mid="qq-mid", title=query, artists=["Artist"], album="Album")]


class CountingNCMusicClient:
    def __init__(self):
        self.calls: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []

    def search_songs(self, context):
        self.calls.append((tuple(context.titles), tuple(context.artists), tuple(context.albums)))
        return [NCMusicCandidate(song_id="ncm-song", title="Song", artists=["Artist"], album="Album")]


class CountingSpotifyClient:
    def __init__(self, delay: float = 0):
        self.delay = delay
        self.calls: list[str | None] = []

    def search_tracks(self, metadata):
        time.sleep(self.delay)
        self.calls.append(metadata.title)
        return [
            SpotifyTrackCandidate(
                track_id="spotify-track",
                title=metadata.title,
                artists=list(metadata.artists),
                album=metadata.album,
                market="US",
            )
        ]


class SearchSchedulerTests(unittest.TestCase):
    def write_ttml(self, directory: Path, name: str) -> Path:
        path = directory / name
        path.write_text(REFERENCE_TTML, encoding="utf-8")
        return path

    def test_prepare_work_items_preserves_order_and_caches_duplicate_source_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_items = [
                WorkItem(self.write_ttml(root, "b.ttml")),
                WorkItem(self.write_ttml(root, "a.ttml")),
            ]
            clients = SearchClients(
                apple_music=CountingAppleMusicClient(),
                qq_music=CountingQQMusicClient(),
                ncm_music=CountingNCMusicClient(),
                spotify=CountingSpotifyClient(),
            )

            prepared, failures = prepare_work_items(work_items, clients, max_workers=2, cache=BatchSearchCache())

        self.assertEqual(failures, [])
        self.assertEqual([pair.ttml_path.name for pair in prepared], ["b.ttml", "a.ttml"])
        self.assertEqual(clients.qq_music.calls, ["Song"])
        self.assertEqual(clients.spotify.calls, ["Song"])
        self.assertEqual(len(clients.apple_music.search_calls), 5)

    def test_prepare_work_item_runs_independent_sources_in_parallel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_items = [WorkItem(self.write_ttml(root, "song.ttml"))]
            clients = SearchClients(
                apple_music=CountingAppleMusicClient(delay=0.12),
                qq_music=CountingQQMusicClient(delay=0.12),
                ncm_music=CountingNCMusicClient(),
                spotify=CountingSpotifyClient(delay=0.12),
            )

            start = time.perf_counter()
            prepared, failures = prepare_work_items(work_items, clients, max_workers=1, cache=BatchSearchCache())
            elapsed = time.perf_counter() - start

        self.assertEqual(failures, [])
        self.assertEqual(len(prepared), 1)
        self.assertLess(elapsed, 0.75)

    def test_collect_ncm_for_pairs_uses_selected_qq_candidate_and_batch_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clients = SearchClients(
                apple_music=CountingAppleMusicClient(),
                qq_music=CountingQQMusicClient(),
                ncm_music=CountingNCMusicClient(),
                spotify=None,
            )
            prepared, failures = prepare_work_items(
                [WorkItem(self.write_ttml(root, "one.ttml")), WorkItem(self.write_ttml(root, "two.ttml"))],
                clients,
                max_workers=2,
                cache=BatchSearchCache(),
            )
            self.assertEqual(failures, [])
            for pair in prepared:
                pair.qq_music_metadata.selected = pair.qq_music_metadata.candidates[0]

            collect_ncm_for_pairs(prepared, clients.ncm_music, max_workers=2, cache=BatchSearchCache())

        self.assertEqual([pair.ncm_music_metadata.selected.song_id for pair in prepared], ["ncm-song", "ncm-song"])
        self.assertEqual(len(clients.ncm_music.calls), 1)


if __name__ == "__main__":
    unittest.main()
