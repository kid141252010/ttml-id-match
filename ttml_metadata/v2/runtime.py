from __future__ import annotations

from collections.abc import Mapping

from ttml_metadata.apple_music import AppleMusicClient
from ttml_metadata.ncm_music import NCMusicClient
from ttml_metadata.qq_music import QQMusicClient
from ttml_metadata.spotify import SpotifyClient, load_spotify_credentials

from .engine import MatchingEngine
from .registry import SourceRegistry
from .sources import (
    AppleMusicSourceAdapter,
    NCMusicSourceAdapter,
    QQMusicSourceAdapter,
    SpotifySourceAdapter,
)
from .transport import HttpTransport, HttpxTransport


DEFAULT_SOURCE_LIMITS: dict[str, int] = {
    "apple_music": 1,
    "qq_music": 2,
    "spotify": 1,
    "ncm_music": 1,
}


def build_default_engine(
    *,
    max_workers: int = 3,
    source_limits: Mapping[str, int] | None = None,
    transport: HttpTransport | None = None,
) -> MatchingEngine:
    shared_transport = transport or HttpxTransport()
    spotify_credentials = load_spotify_credentials()
    spotify_client = (
        SpotifyClient(
            spotify_credentials,
            transport=shared_transport,
            market_workers=1,
        )
        if spotify_credentials.enabled
        else None
    )
    registry = SourceRegistry(
        [
            AppleMusicSourceAdapter(
                AppleMusicClient(transport=shared_transport),
                storefront_workers=1,
            ),
            QQMusicSourceAdapter(QQMusicClient(transport=shared_transport)),
            SpotifySourceAdapter(spotify_client),
            NCMusicSourceAdapter(
                NCMusicClient(
                    transport=shared_transport,
                    api_workers=1,
                    query_workers=1,
                )
            ),
        ]
    )
    return MatchingEngine(
        registry,
        max_workers=max_workers,
        source_limits=dict(source_limits or DEFAULT_SOURCE_LIMITS),
    )
