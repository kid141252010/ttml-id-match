from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

AMLL_NS = "http://www.example.com/ns/amll"


DEFAULT_STORES = ["cn", "us", "kr", "jp", "tw"]


DEFAULT_SPOTIFY_MARKETS = ["US", "KR", "JP", "TW"]


DEFAULT_NCM_API_BASES = [
    "https://music163.xuanmou.com.cn",
    "https://neteasecloudmusicapi-main-api.vercel.app",
    "https://api-enhanced-six-beta.vercel.app",
]


APPLE_MUSIC_SEARCH_LIMIT = 25


APPLE_MUSIC_ARTIST_SEARCH_LIMIT = 10


APPLE_MUSIC_ARTIST_ALBUM_LIMIT = 50


APPLE_MUSIC_ARTIST_ALBUM_PAGE_LIMIT = 10


SPOTIFY_SEARCH_LIMIT = 20


SPOTIFY_CANDIDATE_TARGET = 5


SPOTIFY_ARTIST_SEARCH_LIMIT = 10


SPOTIFY_ARTIST_ALBUM_LIMIT = 10


SPOTIFY_ARTIST_ALBUM_PAGE_LIMIT = 3


SPOTIFY_STRONG_MATCH_SCORE = 260


NCM_SEARCH_LIMIT = 100


NCM_ARTIST_SEARCH_LIMIT = 10


NCM_ARTIST_ALBUM_LIMIT = 50


TARGET_KEY_ORDER = ["musicName", "artists", "album", "qqMusicId", "ncmMusicId", "spotifyId", "appleMusicId", "isrc"]


AUDIO_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".alac",
    ".ape",
    ".flac",
    ".m4a",
    ".m4b",
    ".m4p",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}


@dataclass(frozen=True)
class AudioMetadata:
    title: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    isrc: str | None = None
    catalog_id: str | None = None
    playlist_id: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    duration_seconds: float | None = None
    release_date: str | None = None


@dataclass(frozen=True)
class AppleMusicTrackMatch:
    track: dict[str, Any] | None
    source: str


@dataclass(frozen=True)
class AppleMusicTrackCandidate:
    track_id: str
    title: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    storefront: str = ""
    source_index: int = 0
    isrc: str | None = None
    release_date: str | None = None
    duration_ms: int | None = None
    match_source: str = "search"


@dataclass
class AppleMusicMetadataResult:
    values: dict[str, list[str]] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    candidates: list[AppleMusicTrackCandidate] = field(default_factory=list)
    selected: list[AppleMusicTrackCandidate] = field(default_factory=list)
    candidates_by_storefront: dict[str, list[AppleMusicTrackCandidate]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QQMusicCandidate:
    song_id: str
    mid: str
    title: str | None = None
    subtitle: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    source_index: int = 0


@dataclass
class QQMusicSearchResult:
    candidates: list[QQMusicCandidate] = field(default_factory=list)
    selected: QQMusicCandidate | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NCMusicCandidate:
    song_id: str
    title: str | None = None
    aliases: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    source_index: int = 0


@dataclass(frozen=True)
class NCMusicSearchContext:
    titles: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    albums: list[str] = field(default_factory=list)


@dataclass
class NCMusicSearchResult:
    candidates: list[NCMusicCandidate] = field(default_factory=list)
    selected: NCMusicCandidate | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SpotifyCredentials:
    client_id: str | None = None
    client_secret: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)


@dataclass(frozen=True)
class SpotifyTrackCandidate:
    track_id: str
    title: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    market: str = ""
    source_index: int = 0
    isrc: str | None = None
    duration_ms: int | None = None
    release_date: str | None = None
    release_date_precision: str | None = None
    album_id: str | None = None
    match_source: str = "search"


@dataclass
class SpotifySearchResult:
    candidates: list[SpotifyTrackCandidate] = field(default_factory=list)
    selected: list[SpotifyTrackCandidate] = field(default_factory=list)
    candidates_by_market: dict[str, list[SpotifyTrackCandidate]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _SpotifyArtistCandidate:
    artist_id: str
    name: str | None = None
    source_index: int = 0


@dataclass(frozen=True)
class _SpotifyAlbumCandidate:
    album_id: str
    name: str | None = None
    release_date: str | None = None
    release_date_precision: str | None = None
    source_index: int = 0


@dataclass(frozen=True)
class _NCMusicArtistCandidate:
    artist_id: str
    name: str | None = None
    aliases: list[str] = field(default_factory=list)
    source_index: int = 0


@dataclass(frozen=True)
class _NCMusicAlbumCandidate:
    album_id: str
    name: str | None = None
    source_index: int = 0


@dataclass(frozen=True)
class _AppleMusicArtistCandidate:
    artist_id: str
    name: str | None = None
    source_index: int = 0


@dataclass(frozen=True)
class _AppleMusicAlbumCandidate:
    album_id: str
    name: str | None = None
    release_date: str | None = None
    source_index: int = 0


@dataclass
class TtmlUpdateResult:
    added: dict[str, list[str]] = field(default_factory=dict)
    replaced: dict[str, list[str]] = field(default_factory=dict)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    backup_path: Path | None = None

    @property
    def changed(self) -> bool:
        return bool(self.added or self.replaced)


@dataclass
class TtmlLanguageNormalizationResult:
    language_changed: bool = False
    body_text_changed: bool = False
    removed_translations: int = 0
    removed_transliterations: int = 0
    backup_path: Path | None = None

    @property
    def changed(self) -> bool:
        return bool(
            self.language_changed
            or self.body_text_changed
            or self.removed_translations
            or self.removed_transliterations
        )


@dataclass(frozen=True)
class _XmlAttribute:
    value: str
    value_start: int
    value_end: int


@dataclass(frozen=True)
class _MetaTag:
    start: int
    end: int
    attrs: dict[str, _XmlAttribute]


class AppleMusicClientProtocol(Protocol):
    def fetch_album_tracks(self, store: str, album_id: str) -> list[dict[str, Any]]:
        ...

    def search_songs(self, store: str, metadata: AudioMetadata) -> list[AppleMusicTrackCandidate]:
        ...

    def search_artists(self, store: str, query: str) -> list[_AppleMusicArtistCandidate]:
        ...

    def fetch_artist_albums(self, store: str, artist_id: str) -> tuple[list[_AppleMusicAlbumCandidate], list[str]]:
        ...


class QQMusicClientProtocol(Protocol):
    def search_songs(self, query: str) -> list[QQMusicCandidate]:
        ...


class NCMusicClientProtocol(Protocol):
    def search_songs(self, context: NCMusicSearchContext) -> list[NCMusicCandidate]:
        ...


class SpotifyClientProtocol(Protocol):
    def search_tracks(self, metadata: AudioMetadata) -> list[SpotifyTrackCandidate]:
        ...


class InMemoryAppleMusicClient:
    def __init__(
        self,
        albums: dict[tuple[str, str], list[dict[str, Any]]],
        searches: dict[str, list[AppleMusicTrackCandidate]] | None = None,
        artists: dict[tuple[str, str], list[_AppleMusicArtistCandidate]] | None = None,
        artist_albums: dict[tuple[str, str], list[_AppleMusicAlbumCandidate]] | None = None,
    ):
        self.albums = albums
        self.searches = searches or {}
        self.artists = artists or {}
        self.artist_albums = artist_albums or {}

    def fetch_album_tracks(self, store: str, album_id: str) -> list[dict[str, Any]]:
        tracks = self.albums.get((store, album_id))
        if tracks is None:
            raise LookupError(f"album {album_id} not found in {store}")
        return tracks

    def search_songs(self, store: str, metadata: AudioMetadata) -> list[AppleMusicTrackCandidate]:
        return list(self.searches.get(store, []))

    def search_artists(self, store: str, query: str) -> list[_AppleMusicArtistCandidate]:
        return list(self.artists.get((store, query), []))

    def fetch_artist_albums(self, store: str, artist_id: str) -> tuple[list[_AppleMusicAlbumCandidate], list[str]]:
        return list(self.artist_albums.get((store, artist_id), [])), []
