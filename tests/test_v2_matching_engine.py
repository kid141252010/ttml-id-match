import threading
import time
import unittest

from ttml_metadata.models import AudioMetadata
from ttml_metadata.v2.domain import Candidate, MatchContext, MatchEvidence, Selection, SourceResult
from ttml_metadata.v2.engine import MatchingEngine, UnknownCandidateError
from ttml_metadata.v2.sources import QQMusicSourceAdapter
from ttml_metadata.models import QQMusicCandidate


class RecordingSource:
    def __init__(self, key, events, *, dependencies=(), fail=False, delay=0.0):
        self.key = key
        self.dependencies = frozenset(dependencies)
        self.events = events
        self.fail = fail
        self.delay = delay

    def search(self, context: MatchContext) -> SourceResult:
        self.events.append((self.key, "start", tuple(context.results)))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"{self.key} unavailable")
        candidate = Candidate(
            id=f"{self.key}-1",
            source=self.key,
            title=context.metadata.title,
            artists=tuple(context.metadata.artists),
            album=context.metadata.album,
            rank=1,
            recommended=True,
            evidence=(MatchEvidence(field="title", relation="exact"),),
        )
        self.events.append((self.key, "finish", tuple(context.results)))
        return SourceResult(
            source=self.key,
            candidates=(candidate,),
            groups={"default": (candidate.id,)},
            recommended_ids=(candidate.id,),
        )

    def metadata_values(self, metadata, result, selected_ids):
        return {}


class MatchingEngineTests(unittest.TestCase):
    def test_runs_dependency_waves_and_keeps_source_failures_as_warnings(self):
        events = []
        lock = threading.Lock()

        class ThreadSafeEvents(list):
            def append(self, value):
                with lock:
                    super().append(value)

        events = ThreadSafeEvents()
        adapters = [
            RecordingSource("apple_music", events, delay=0.03),
            RecordingSource("qq_music", events, fail=True, delay=0.01),
            RecordingSource("spotify", events, delay=0.02),
            RecordingSource("ncm_music", events, dependencies=("qq_music",)),
        ]

        result = MatchingEngine(adapters, max_workers=3).match(AudioMetadata(title="Song"))

        self.assertEqual(tuple(result.sources), ("apple_music", "qq_music", "spotify", "ncm_music"))
        self.assertEqual(result.sources["qq_music"].candidates, ())
        self.assertEqual(result.sources["qq_music"].warnings, ("qq_music unavailable",))
        ncm_start = next(event for event in events if event[0:2] == ("ncm_music", "start"))
        self.assertEqual(ncm_start[2], ("apple_music", "qq_music", "spotify"))
        self.assertEqual(result.sources["ncm_music"].recommended_ids, ("ncm_music-1",))

    def test_malformed_source_results_become_source_warnings(self):
        class MalformedSource(RecordingSource):
            def search(self, context):
                candidate = Candidate(id="duplicate", source=self.key)
                return SourceResult(
                    source=self.key,
                    candidates=(candidate, candidate),
                )

        result = MatchingEngine([MalformedSource("qq_music", [])]).match(
            AudioMetadata(title="Song")
        )

        self.assertEqual(result.sources["qq_music"].candidates, ())
        self.assertEqual(
            result.sources["qq_music"].warnings,
            ("source adapter qq_music returned duplicate candidate ids",),
        )

    def test_builds_default_selection_and_metadata_values_from_public_candidates(self):
        class QQClient:
            def search_songs(self, _query):
                return [QQMusicCandidate("qq-1", "mid-1", title="Song", artists=["Artist"], album="Album")]

        engine = MatchingEngine([QQMusicSourceAdapter(QQClient())])
        result = engine.match(AudioMetadata(title="Song", artists=["Artist"], album="Album", isrc="ISRC-1"))

        selection = engine.default_selection("pair-1", result)
        values = engine.metadata_values(result, selection)

        self.assertEqual(selection, Selection(pair_id="pair-1", sources={"qq_music": ("qq-1",)}))
        self.assertEqual(values["musicName"], ["Song"])
        self.assertEqual(values["artists"], ["Artist"])
        self.assertEqual(values["album"], ["Album"])
        self.assertEqual(values["qqMusicId"], ["qq-1", "mid-1"])
        self.assertEqual(values["isrc"], ["ISRC-1"])

        with self.assertRaises(UnknownCandidateError):
            engine.metadata_values(result, Selection("pair-1", {"qq_music": ("missing",)}))

    def test_match_many_preserves_order_and_coalesces_duplicate_queries(self):
        calls = []

        class CountingSource(RecordingSource):
            def search(self, context):
                calls.append(context.metadata.title)
                return super().search(context)

        source = CountingSource("qq_music", [])
        engine = MatchingEngine([source], max_workers=2)

        results = engine.match_many(
            [
                AudioMetadata(title="B"),
                AudioMetadata(title="A"),
                AudioMetadata(title="B"),
            ]
        )

        self.assertEqual([result.metadata.title for result in results], ["B", "A", "B"])
        self.assertEqual(sorted(calls), ["A", "B"])

    def test_global_budget_and_source_limit_bound_concurrent_adapter_calls(self):
        lock = threading.Lock()
        active = 0
        peak = 0
        active_by_source = {"apple_music": 0, "qq_music": 0}
        peak_by_source = {"apple_music": 0, "qq_music": 0}

        class LimitedSource(RecordingSource):
            def search(self, context):
                nonlocal active, peak
                with lock:
                    active += 1
                    active_by_source[self.key] += 1
                    peak = max(peak, active)
                    peak_by_source[self.key] = max(
                        peak_by_source[self.key], active_by_source[self.key]
                    )
                try:
                    time.sleep(0.02)
                    return super().search(context)
                finally:
                    with lock:
                        active -= 1
                        active_by_source[self.key] -= 1

        engine = MatchingEngine(
            [LimitedSource("apple_music", []), LimitedSource("qq_music", [])],
            max_workers=3,
            source_limits={"apple_music": 1, "qq_music": 2},
            cache_size=0,
        )

        engine.match_many([AudioMetadata(title=str(index)) for index in range(5)])

        self.assertLessEqual(peak, 3)
        self.assertLessEqual(peak_by_source["apple_music"], 1)
        self.assertLessEqual(peak_by_source["qq_music"], 2)

    def test_dependent_cache_key_includes_the_upstream_result(self):
        class MutableQQ(RecordingSource):
            def __init__(self):
                super().__init__("qq_music", [])
                self.version = 1

            def search(self, context):
                candidate = Candidate(
                    id=f"qq-{self.version}",
                    source=self.key,
                    recommended=True,
                )
                return SourceResult(
                    source=self.key,
                    candidates=(candidate,),
                    recommended_ids=(candidate.id,),
                )

        class DependentNCM(RecordingSource):
            def __init__(self):
                super().__init__("ncm_music", [], dependencies=("qq_music",))
                self.calls = 0

            def search(self, context):
                self.calls += 1
                qq_id = context.results["qq_music"].recommended_ids[0]
                candidate = Candidate(id=f"ncm-for-{qq_id}", source=self.key)
                return SourceResult(source=self.key, candidates=(candidate,))

        qq = MutableQQ()
        ncm = DependentNCM()
        engine = MatchingEngine([qq, ncm], cache_ttl_seconds=60)
        metadata = AudioMetadata(title="Song")

        first = engine.match(metadata)
        with engine._cache_lock:
            qq_keys = [key for key in engine._cache if key[0] == "qq_music"]
            for key in qq_keys:
                del engine._cache[key]
        qq.version = 2
        second = engine.match(metadata)

        self.assertEqual(first.sources["ncm_music"].candidates[0].id, "ncm-for-qq-1")
        self.assertEqual(second.sources["ncm_music"].candidates[0].id, "ncm-for-qq-2")
        self.assertEqual(ncm.calls, 2)


if __name__ == "__main__":
    unittest.main()
