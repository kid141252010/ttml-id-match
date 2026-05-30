import tempfile
import unittest
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


class ServerApiTests(unittest.TestCase):
    def make_client(self):
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
            qq_music_client=FakeQQMusicClient(),
            ncm_music_client=FakeNCMusicClient(),
            spotify_client=FakeSpotifyClient(),
            search_workers=1,
        )
        return TestClient(create_app(session_manager=session_manager, metadata_service=metadata_service))

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
if __name__ == "__main__":
    unittest.main()


