import tempfile
import unittest
import os
from pathlib import Path

from fastapi.testclient import TestClient

from server.main import create_app
from server.services.metadata_service import MetadataService
from server.services.session_manager import SessionManager
from ttml_metadata.models import (
    AppleMusicTrackCandidate,
    InMemoryAppleMusicClient,
    NCMusicCandidate,
    QQMusicCandidate,
    SpotifyTrackCandidate,
)

REFERENCE_STYLE_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
    'xmlns:amll="http://www.example.com/ns/amll" '
    'xml:lang="zh-Hans">'
    '<head><metadata>'
    '<ttm:agent type="person" xml:id="v1"/>'
    '<amll:meta key="musicName" value="Song"/>'
    '<amll:meta key="artists" value="Artist"/>'
    '<amll:meta key="album" value="Album"/>'
    '<iTunesMetadata><songwriters><songwriter>A</songwriter></songwriters></iTunesMetadata>'
    '</metadata></head>'
    '<body dur="01:00.000"><div><p begin="00:00.000" end="00:01.000">x</p></div></body>'
    '</tt>'
)


class FakeQQMusicClient:
    def search_songs(self, query: str):
        return [
            QQMusicCandidate(
                song_id="qq-song-1",
                mid="qq-mid-1",
                title=query,
                artists=["Artist"],
                album="Album",
            )
        ]


class CountingQQMusicClient(FakeQQMusicClient):
    def __init__(self):
        self.calls: list[str] = []

    def search_songs(self, query: str):
        self.calls.append(query)
        return [
            QQMusicCandidate(
                song_id=f"qq-song-{len(self.calls)}",
                mid=f"qq-mid-{len(self.calls)}",
                title=query,
                artists=["Artist"],
                album="Album",
            )
        ]


class FakeNCMusicClient:
    def search_songs(self, context):
        return [
            NCMusicCandidate(
                song_id="ncm-song-1",
                title="Song",
                artists=["Artist"],
                album="Album",
            )
        ]


class CountingNCMusicClient(FakeNCMusicClient):
    def __init__(self):
        self.calls = []

    def search_songs(self, context):
        self.calls.append(context)
        return super().search_songs(context)


class FakeSpotifyClient:
    def search_tracks(self, metadata):
        return [
            SpotifyTrackCandidate(
                track_id="spotify-track-1",
                title=metadata.title,
                artists=list(metadata.artists),
                album=metadata.album,
                market="US",
                isrc="USZZZ2600001",
            )
        ]


class CountingSpotifyClient(FakeSpotifyClient):
    def __init__(self):
        self.calls: list[str | None] = []

    def search_tracks(self, metadata):
        self.calls.append(metadata.title)
        return super().search_tracks(metadata)


class ServerApiTests(unittest.TestCase):
    def make_client_bundle(
        self,
        *,
        qq_music_client=None,
        ncm_music_client=None,
        spotify_client=None,
    ):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        apple_client = InMemoryAppleMusicClient(
            albums={},
            searches={
                "cn": [
                    AppleMusicTrackCandidate(
                        track_id="apple-song-1",
                        title="Song",
                        artists=["Artist"],
                        album="Album",
                        storefront="cn",
                        isrc="USZZZ2600001",
                    )
                ]
            },
        )
        session_manager = SessionManager(Path(tmp.name))
        metadata_service = MetadataService(
            session_manager,
            apple_music_client=apple_client,
            qq_music_client=qq_music_client or FakeQQMusicClient(),
            ncm_music_client=ncm_music_client or FakeNCMusicClient(),
            spotify_client=spotify_client or FakeSpotifyClient(),
            search_workers=1,
        )
        client = TestClient(create_app(session_manager=session_manager, metadata_service=metadata_service))
        return client, session_manager, metadata_service

    def make_client(self):
        client, _session_manager, _metadata_service = self.make_client_bundle()
        return client

    def test_upload_preview_apply_and_download_ttml(self):
        client = self.make_client()

        session_id = client.post("/api/sessions").json()["session_id"]
        upload = client.post(
            f"/api/sessions/{session_id}/upload",
            files=[("files", ("Song.ttml", REFERENCE_STYLE_TTML.encode("utf-8"), "application/xml"))],
        )
        self.assertEqual(upload.status_code, 200)
        self.assertEqual(upload.json()["pairs"][0]["status"], "ttml_only")

        preview = client.post(f"/api/sessions/{session_id}/preview")
        self.assertEqual(preview.status_code, 200)
        preview_body = preview.json()
        result = preview_body["results"][0]
        self.assertEqual(result["ttml"], "Song.ttml")
        self.assertEqual(result["apple_music"]["best"][0]["id"], "apple-song-1")
        self.assertEqual(result["qq_music"]["best"][0]["id"], "qq-song-1")
        self.assertIn("appleMusicId", result["changes"]["added"])

        apply_response = client.post(
            f"/api/sessions/{session_id}/apply",
            json={
                "selections": [
                    {
                        "pair_id": result["pair_id"],
                        "apple_music": ["apple-song-1"],
                        "qq_music": ["qq-song-1"],
                        "ncm_music": ["ncm-song-1"],
                        "spotify": ["spotify-track-1"],
                    }
                ]
            },
        )
        self.assertEqual(apply_response.status_code, 200)
        self.assertEqual(apply_response.json()["succeeded"], 1)

        downloaded = client.get(f"/api/sessions/{session_id}/download/Song.ttml")
        self.assertEqual(downloaded.status_code, 200)
        text = downloaded.content.decode("utf-8")
        self.assertIn('key="appleMusicId" value="apple-song-1"', text)
        self.assertIn('key="qqMusicId" value="qq-song-1"', text)
        self.assertIn('key="qqMusicId" value="qq-mid-1"', text)
        self.assertIn('key="ncmMusicId" value="ncm-song-1"', text)
        self.assertIn('key="spotifyId" value="spotify-track-1"', text)

        zip_response = client.get(f"/api/sessions/{session_id}/download")
        self.assertEqual(zip_response.status_code, 200)
        self.assertEqual(zip_response.headers["content-type"], "application/zip")


    def test_preview_rejects_ttml_only_without_title_metadata(self):
        client = self.make_client()
        session_id = client.post("/api/sessions").json()["session_id"]
        no_title_ttml = REFERENCE_STYLE_TTML.replace('<amll:meta key="musicName" value="Song"/>', '')
        upload = client.post(
            f"/api/sessions/{session_id}/upload",
            files=[("files", ("NoTitle.ttml", no_title_ttml.encode("utf-8"), "application/xml"))],
        )
        self.assertEqual(upload.status_code, 200)

        preview = client.post(f"/api/sessions/{session_id}/preview")

        self.assertEqual(preview.status_code, 400)
        self.assertIn("TTML 中未读取到歌名", preview.json()["detail"])

    def test_preview_reuses_cached_response_when_uploaded_files_are_unchanged(self):
        qq_client = CountingQQMusicClient()
        client, session_manager, _metadata_service = self.make_client_bundle(qq_music_client=qq_client)
        session_id = client.post("/api/sessions").json()["session_id"]
        upload = client.post(
            f"/api/sessions/{session_id}/upload",
            files=[("files", ("Song.ttml", REFERENCE_STYLE_TTML.encode("utf-8"), "application/xml"))],
        )
        self.assertEqual(upload.status_code, 200)

        first = client.post(f"/api/sessions/{session_id}/preview")
        self.assertEqual(first.status_code, 200)
        state = session_manager.get(session_id)
        state.previews["pair-1"].changes.skipped["cacheMarker"] = ["hit"]
        first_call_count = len(qq_client.calls)

        second = client.post(f"/api/sessions/{session_id}/preview")

        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(qq_client.calls), first_call_count)
        self.assertEqual(second.json()["results"][0]["changes"]["skipped"]["cacheMarker"], ["hit"])

    def test_upload_clears_cached_preview_state(self):
        client, session_manager, _metadata_service = self.make_client_bundle()
        session_id = client.post("/api/sessions").json()["session_id"]
        upload = client.post(
            f"/api/sessions/{session_id}/upload",
            files=[("files", ("Song.ttml", REFERENCE_STYLE_TTML.encode("utf-8"), "application/xml"))],
        )
        self.assertEqual(upload.status_code, 200)
        self.assertEqual(client.post(f"/api/sessions/{session_id}/preview").status_code, 200)
        state = session_manager.get(session_id)
        self.assertTrue(state.previews)
        self.assertTrue(state.prepared_pairs)
        self.assertIsNotNone(state.preview_fingerprint)

        next_ttml = REFERENCE_STYLE_TTML.replace('value="Song"', 'value="Another Song"')
        upload = client.post(
            f"/api/sessions/{session_id}/upload",
            files=[("files", ("Another.ttml", next_ttml.encode("utf-8"), "application/xml"))],
        )

        self.assertEqual(upload.status_code, 200)
        state = session_manager.get(session_id)
        self.assertEqual(state.previews, {})
        self.assertEqual(state.prepared_pairs, {})
        self.assertIsNone(state.preview_fingerprint)

    def test_preview_job_steps_through_pairs_incrementally(self):
        client = self.make_client()
        session_id = client.post("/api/sessions").json()["session_id"]
        second_ttml = REFERENCE_STYLE_TTML.replace('value="Song"', 'value="Another Song"')
        upload = client.post(
            f"/api/sessions/{session_id}/upload",
            files=[
                ("files", ("A.ttml", REFERENCE_STYLE_TTML.encode("utf-8"), "application/xml")),
                ("files", ("B.ttml", second_ttml.encode("utf-8"), "application/xml")),
            ],
        )
        self.assertEqual(upload.status_code, 200)

        created = client.post(f"/api/sessions/{session_id}/preview-jobs")
        self.assertEqual(created.status_code, 200)
        job = created.json()
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["total"], 2)
        self.assertEqual(job["completed"], 0)

        first = client.post(f"/api/sessions/{session_id}/preview-jobs/{job['job_id']}/step")
        self.assertEqual(first.status_code, 200)
        first_body = first.json()
        self.assertEqual(first_body["status"], "running")
        self.assertEqual(first_body["completed"], 1)
        self.assertEqual([result["ttml"] for result in first_body["results"]], ["A.ttml"])

        second = client.post(f"/api/sessions/{session_id}/preview-jobs/{job['job_id']}/step")
        self.assertEqual(second.status_code, 200)
        second_body = second.json()
        self.assertEqual(second_body["status"], "complete")
        self.assertEqual(second_body["completed"], 2)
        self.assertEqual([result["ttml"] for result in second_body["results"]], ["A.ttml", "B.ttml"])

    def test_preview_job_failure_preserves_completed_results(self):
        client = self.make_client()
        session_id = client.post("/api/sessions").json()["session_id"]
        no_title_ttml = REFERENCE_STYLE_TTML.replace('<amll:meta key="musicName" value="Song"/>', '')
        upload = client.post(
            f"/api/sessions/{session_id}/upload",
            files=[
                ("files", ("A.ttml", REFERENCE_STYLE_TTML.encode("utf-8"), "application/xml")),
                ("files", ("B.ttml", no_title_ttml.encode("utf-8"), "application/xml")),
            ],
        )
        self.assertEqual(upload.status_code, 200)
        job = client.post(f"/api/sessions/{session_id}/preview-jobs").json()
        self.assertEqual(client.post(f"/api/sessions/{session_id}/preview-jobs/{job['job_id']}/step").status_code, 200)

        failed = client.post(f"/api/sessions/{session_id}/preview-jobs/{job['job_id']}/step")

        self.assertEqual(failed.status_code, 200)
        failed_body = failed.json()
        self.assertEqual(failed_body["status"], "failed")
        self.assertEqual(failed_body["completed"], 1)
        self.assertEqual([result["ttml"] for result in failed_body["results"]], ["A.ttml"])
        self.assertIn("B.ttml", failed_body["error"])
        self.assertIn("TTML 中未读取到歌名", failed_body["error"])

    def test_preview_job_does_not_fail_when_rehydrated_file_mtime_changes(self):
        client, session_manager, _metadata_service = self.make_client_bundle()
        session_id = client.post("/api/sessions").json()["session_id"]
        upload = client.post(
            f"/api/sessions/{session_id}/upload",
            files=[("files", ("Song.ttml", REFERENCE_STYLE_TTML.encode("utf-8"), "application/xml"))],
        )
        self.assertEqual(upload.status_code, 200)
        job = client.post(f"/api/sessions/{session_id}/preview-jobs").json()
        ttml_path = session_manager.get(session_id).upload_dir / "Song.ttml"
        stat = ttml_path.stat()
        os.utime(ttml_path, (stat.st_atime, stat.st_mtime + 5))

        stepped = client.post(f"/api/sessions/{session_id}/preview-jobs/{job['job_id']}/step")

        self.assertEqual(stepped.status_code, 200)
        stepped_body = stepped.json()
        self.assertEqual(stepped_body["status"], "complete")
        self.assertEqual(stepped_body["completed"], 1)
if __name__ == "__main__":
    unittest.main()


