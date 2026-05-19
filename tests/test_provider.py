from __future__ import annotations

import hashlib

import pytest

from remote_library_client.provider import DirectLibraryProvider, provider_id_for_source

SONGS = [
    {
        "sourceId": "direct_studio",
        "remoteSongId": "song-one",
        "title": "Clean Tone",
        "artist": "The Fixtures",
        "album": "Bench",
        "format": "psarc",
        "packageForm": "psarc-file",
        "packageHash": "sha256:" + hashlib.sha256(b"package-one").hexdigest(),
        "arrangements": [{"name": "Lead"}],
        "has_lyrics": True,
        "tuning": "E Standard",
    },
    {
        "sourceId": "direct_studio",
        "remoteSongId": "song-two",
        "title": "Heavy Tone",
        "artist": "The Fixtures",
        "album": "Bench",
        "format": "sloppak",
        "packageForm": "sloppak-zip",
        "packageHash": "sha256:" + hashlib.sha256(b"package-two").hexdigest(),
        "arrangements": [{"name": "Rhythm"}],
        "has_lyrics": False,
        "tuning": "Drop D",
    },
]


class FakeProvider(DirectLibraryProvider):
    def __init__(self, tmp_path):
        super().__init__({
            "baseUrl": "https://studio.example.test",
            "providerId": provider_id_for_source("direct_studio", "https://studio.example.test"),
            "sourceId": "direct_studio",
            "label": "Studio",
        }, tmp_path)

    def _json(self, path: str, params: dict | None = None) -> dict:
        if path != "/songs":
            raise AssertionError(path)
        q = (params or {}).get("q") or ""
        songs = [song for song in SONGS if q.lower() in song["title"].lower()]
        return {"songs": songs, "nextCursor": None}

    def _bytes(self, path: str, params: dict | None = None):
        if path.endswith("/art"):
            return b"art-bytes", "image/png", {}
        if path.endswith("/package"):
            song_id = path.split("/")[-2]
            content = b"package-one" if song_id == "song-one" else b"package-two"
            return (
                content,
                "application/octet-stream",
                {"content-disposition": f'attachment; filename="{song_id}.psarc"'},
            )
        raise AssertionError(path)


def test_query_page_filters_and_normalizes(tmp_path):
    provider = FakeProvider(tmp_path)

    songs, total = provider.query_page(q="tone", size=10, arrangements_has=["Lead"], tunings=["E Standard"])

    assert total == 1
    assert songs[0]["filename"] == "song-one"
    assert songs[0]["song_id"] == "song-one"
    assert songs[0]["libraryProviderId"] == provider.id
    assert songs[0]["artist"] == "The Fixtures"


def test_artist_stats_and_tunings(tmp_path):
    provider = FakeProvider(tmp_path)

    artists, total_artists = provider.query_artists(size=10)
    stats = provider.query_stats()
    tunings = provider.tuning_names()

    assert total_artists == 1
    assert artists[0]["name"] == "The Fixtures"
    assert artists[0]["album_count"] == 1
    assert stats == {"total_songs": 2, "total_artists": 1, "letters": {"T": 1}}
    assert {item["name"] for item in tunings["tunings"]} == {"E Standard", "Drop D"}


def test_art_proxy_returns_response(tmp_path):
    provider = FakeProvider(tmp_path)

    response = provider.get_art("song-one")

    assert response.body == b"art-bytes"
    assert response.media_type == "image/png"


def test_sync_downloads_to_plugin_cache(tmp_path):
    provider = FakeProvider(tmp_path)

    result = provider.sync_song("song-one")

    cached = tmp_path / provider.cache_dir.name / "song-one.psarc"

    assert result["ok"] is True
    assert result["cachedPath"] == str(cached)
    assert cached.read_bytes() == b"package-one"


def test_sync_rejects_hash_mismatch(tmp_path):
    provider = FakeProvider(tmp_path)
    original_bytes = provider._bytes

    def wrong_bytes(path: str, params: dict | None = None):
        if path.endswith("/package"):
            return b"wrong", "application/octet-stream", {}
        return original_bytes(path, params)

    provider._bytes = wrong_bytes

    with pytest.raises(RuntimeError, match="hash"):
        provider.sync_song("song-one")