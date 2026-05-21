from __future__ import annotations

import json
import sqlite3

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
        "arrangements": [{"name": "Rhythm"}],
        "has_lyrics": False,
        "stem_count": 4,
        "stem_ids": ["drums", "bass", "guitar", "vocals"],
        "tuning": "Drop D",
    },
]


class FakeProvider(DirectLibraryProvider):
    def __init__(self, tmp_path, local_library_root=None, library_importer=None, source_extra=None, nam_config_dir=None):
        source = {
            "baseUrl": "https://studio.example.test",
            "providerId": provider_id_for_source("direct_studio", "https://studio.example.test"),
            "sourceId": "direct_studio",
            "label": "Studio",
        }
        source.update(source_extra or {})
        super().__init__(source, tmp_path, local_library_root, library_importer, nam_config_dir)
        self.json_calls: list[tuple[str, dict]] = []
        self.bytes_calls: list[str] = []

    def _json(self, path: str, params: dict | None = None, timeout: float = 20) -> dict:
        params = params or {}
        self.json_calls.append((path, dict(params)))
        if path.startswith("/songs/"):
            if path.endswith("/nam-tone-sync"):
                return {
                    "schema": "slopsmith.nam-tone-sync.v1",
                    "sourceId": "direct_studio",
                    "remoteSongId": "song-one",
                    "sourceFilename": "song-one.psarc",
                    "mappings": [{"toneKey": "Clean", "presetRef": "preset:clean"}],
                    "presets": [{
                        "ref": "preset:clean",
                        "name": "Clean NAM",
                        "modelFile": {
                            "name": "clean.nam",
                            "sizeBytes": len(b'{"model":"clean"}'),
                            "sha256": "sha256:ad5beddb785715813f7466bc58c6b6a2e4b2391743485d2ea805dd4ffdaf4428",
                            "url": "/songs/song-one/nam-tone-assets/model/clean.nam",
                        },
                        "irFile": {
                            "name": "room.wav",
                            "sizeBytes": len(b"RIFF-room"),
                            "sha256": "sha256:4dc883e3c126726807dc5a6b035fbcac6739613c9d977e27377ed0b49dc55b7a",
                            "url": "/songs/song-one/nam-tone-assets/ir/room.wav",
                        },
                        "inputGain": 1.25,
                        "outputGain": 0.75,
                        "gateThreshold": -55.0,
                        "settings": {"cab": "open"},
                    }],
                    "warnings": [],
                }
            song_id = path.split("/", 2)[-1]
            song = next((item for item in SONGS if item["remoteSongId"] == song_id), None)
            if not song:
                raise RuntimeError('{"detail":"song not found"}')
            return dict(song)
        if path == "/artists":
            return {
                "artists": [{
                    "name": "The Fixtures",
                    "album_count": 1,
                    "song_count": len(SONGS),
                    "albums": [{"name": "Bench", "songs": SONGS}],
                }],
                "total_artists": 1,
                "query": {"filtersApplied": True},
            }
        if path == "/stats":
            return {
                "total_songs": len(SONGS),
                "total_artists": 1,
                "letters": {"T": 1},
                "query": {"filtersApplied": True},
            }
        if path == "/tuning-names":
            return {"tunings": [
                {"name": "Drop D", "sort_key": 0, "count": 1},
                {"name": "E Standard", "sort_key": 0, "count": 1},
            ]}
        if path != "/songs":
            raise AssertionError(path)
        q = params.get("q") or ""
        songs = [song for song in SONGS if q.lower() in song["title"].lower()]
        arrangements_has = {item for item in str(params.get("arrangements_has") or "").split(",") if item}
        stems_has = {item for item in str(params.get("stems_has") or "").split(",") if item}
        stems_lacks = {item for item in str(params.get("stems_lacks") or "").split(",") if item}
        tunings = {item for item in str(params.get("tunings") or "").split(",") if item}
        if arrangements_has:
            songs = [
                song for song in songs
                if arrangements_has.intersection({item.get("name") for item in song.get("arrangements") or []})
            ]
        if stems_has:
            songs = [song for song in songs if stems_has.issubset(set(song.get("stem_ids") or []))]
        if stems_lacks:
            songs = [song for song in songs if not stems_lacks.intersection(set(song.get("stem_ids") or []))]
        if tunings:
            songs = [song for song in songs if song.get("tuning") in tunings]
        page_size = int(params.get("pageSize") or len(songs) or 1)
        page = int(params.get("page") or 0)
        offset = page * page_size
        return {
            "songs": songs[offset:offset + page_size],
            "total": len(songs),
            "nextCursor": str(offset + page_size) if offset + page_size < len(songs) else None,
            "query": {"filtersApplied": True},
        }

    def _bytes(self, path: str, params: dict | None = None):
        self.bytes_calls.append(path)
        if path.endswith("/nam-tone-assets/model/clean.nam"):
            return b'{"model":"clean"}', "application/json", {}
        if path.endswith("/nam-tone-assets/ir/room.wav"):
            return b"RIFF-room", "audio/wav", {}
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


def test_repeated_metadata_queries_use_cache(tmp_path):
    provider = FakeProvider(tmp_path)

    first_songs, first_total = provider.query_page(q="tone", size=10)
    second_songs, second_total = provider.query_page(q="tone", size=10)
    provider.query_stats()
    provider.query_stats()
    provider.tuning_names()
    provider.tuning_names()

    assert first_total == second_total == 2
    assert first_songs == second_songs
    assert [path for path, _params in provider.json_calls].count("/songs") == 1
    assert [path for path, _params in provider.json_calls].count("/stats") == 1
    assert [path for path, _params in provider.json_calls].count("/tuning-names") == 1


def test_metadata_cache_can_be_cleared(tmp_path):
    provider = FakeProvider(tmp_path)

    provider.query_page(q="tone", size=10)
    provider.clear_metadata_cache()
    provider.query_page(q="tone", size=10)

    assert [path for path, _params in provider.json_calls].count("/songs") == 2


def test_query_page_preserves_stem_metadata_and_filters(tmp_path):
    provider = FakeProvider(tmp_path)

    songs, total = provider.query_page(q="tone", size=10, stems_has=["drums"], stems_lacks=["piano"])

    assert total == 1
    assert songs[0]["filename"] == "song-two"
    assert songs[0]["stem_count"] == 4
    assert songs[0]["stem_ids"] == ["drums", "bass", "guitar", "vocals"]


def test_query_page_does_not_scan_local_library_for_matching_package(tmp_path):
    local_root = tmp_path / "dlc"
    local_root.mkdir()
    (local_root / "local-song-one.psarc").write_bytes(b"package-one")
    provider = FakeProvider(tmp_path / "cache", local_root)

    songs, total = provider.query_page(q="clean", size=10)

    assert total == 1
    assert songs[0]["localFilename"] == ""
    assert songs[0]["local_filename"] == ""
    assert songs[0]["playFilename"] == ""


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


def test_missing_remote_art_returns_none(tmp_path):
    provider = FakeProvider(tmp_path)

    def missing_art(path: str, params: dict | None = None):
        if path.endswith("/art"):
            raise RuntimeError('{"detail":"artwork not found"}')
        raise AssertionError(path)

    provider._bytes = missing_art

    assert provider.get_art("song-one") is None


def test_sync_downloads_to_plugin_cache(tmp_path):
    provider = FakeProvider(tmp_path)

    result = provider.sync_song("song-one")

    cached = tmp_path / provider.cache_dir.name / "song-one.psarc"

    assert result["ok"] is True
    assert result["playbackSource"] == "remote-cache"
    assert result["cachedPath"] == str(cached)
    assert cached.read_bytes() == b"package-one"


def test_sync_imports_to_local_library_when_configured(tmp_path):
    local_root = tmp_path / "dlc"
    local_root.mkdir()
    provider = FakeProvider(tmp_path / "cache", local_root)

    result = provider.sync_song("song-one")

    assert result["ok"] is True
    assert result["playbackSource"] == "library-folder"
    assert result["filename"] == "direct_studio/song-one.psarc"
    assert result["localFilename"] == "direct_studio/song-one.psarc"
    assert result["playFilename"] == "direct_studio/song-one.psarc"
    assert (local_root / "direct_studio" / "song-one.psarc").read_bytes() == b"package-one"


def test_sync_imports_enabled_nam_tone_assets_and_mappings(tmp_path):
    local_root = tmp_path / "dlc"
    local_root.mkdir()
    provider = FakeProvider(
        tmp_path / "cache",
        local_root,
        source_extra={"syncNamToneAssets": True},
        nam_config_dir=tmp_path / "config",
    )

    result = provider.sync_song("song-one")

    assert result["toneSync"] == {
        "ok": True,
        "skipped": False,
        "presetsImported": 1,
        "mappingsImported": 1,
        "assetsImported": 2,
        "assetsReused": 0,
        "warnings": [],
    }
    assert (tmp_path / "config" / "nam_models" / "clean.nam").read_bytes() == b'{"model":"clean"}'
    assert (tmp_path / "config" / "nam_irs" / "room.wav").read_bytes() == b"RIFF-room"
    conn = sqlite3.connect(tmp_path / "config" / "nam_tone.db")
    preset = conn.execute(
        "SELECT id, name, model_file, ir_file, input_gain, output_gain, gate_threshold, settings_json FROM presets"
    ).fetchone()
    mapping = conn.execute("SELECT filename, tone_key, preset_id FROM tone_mappings").fetchone()
    conn.close()
    assert preset[1:7] == ("Studio / Clean NAM", "clean.nam", "room.wav", 1.25, 0.75, -55.0)
    settings = json.loads(preset[7])
    assert settings["cab"] == "open"
    assert settings["remoteLibraryClient"]["remotePresetRef"] == "preset:clean"
    assert mapping == ("direct_studio/song-one.psarc", "Clean", preset[0])


def test_sync_skips_nam_tone_assets_when_source_setting_disabled(tmp_path):
    local_root = tmp_path / "dlc"
    local_root.mkdir()
    provider = FakeProvider(tmp_path / "cache", local_root, nam_config_dir=tmp_path / "config")

    result = provider.sync_song("song-one")

    assert "toneSync" not in result
    assert "/songs/song-one/nam-tone-sync" not in [path for path, _params in provider.json_calls]


def test_sync_reports_nam_tone_errors_without_failing_song_sync(tmp_path):
    local_root = tmp_path / "dlc"
    local_root.mkdir()
    provider = FakeProvider(
        tmp_path / "cache",
        local_root,
        source_extra={"syncNamToneAssets": True},
        nam_config_dir=tmp_path / "config",
    )
    original_json = provider._json

    def broken_json(path: str, params: dict | None = None, timeout: float = 20) -> dict:
        if path.endswith("/nam-tone-sync"):
            raise RuntimeError("manifest exploded")
        return original_json(path, params, timeout)

    provider._json = broken_json

    result = provider.sync_song("song-one")

    assert result["ok"] is True
    assert result["filename"] == "direct_studio/song-one.psarc"
    assert result["toneSync"] == {"ok": False, "skipped": False, "error": "manifest exploded"}


def test_sync_indexes_local_library_file_when_importer_available(tmp_path):
    local_root = tmp_path / "dlc"
    local_root.mkdir()
    imported = []

    def import_library_file(package_path, root):
        imported.append((package_path, root))
        return {"libraryImportState": "indexed", "libraryFilename": package_path.relative_to(root).as_posix()}

    provider = FakeProvider(tmp_path / "cache", local_root, import_library_file)

    result = provider.sync_song("song-one")

    assert imported == [(local_root / "direct_studio" / "song-one.psarc", local_root)]
    assert result["libraryImportState"] == "indexed"
    assert result["libraryFilename"] == "direct_studio/song-one.psarc"


def test_sync_allocates_unique_local_library_name_on_content_conflict(tmp_path):
    local_root = tmp_path / "dlc"
    target_dir = local_root / "direct_studio"
    target_dir.mkdir(parents=True)
    (target_dir / "song-one.psarc").write_bytes(b"different")
    provider = FakeProvider(tmp_path / "cache", local_root)

    result = provider.sync_song("song-one")

    assert result["filename"] == "direct_studio/song-one-2.psarc"
    assert (target_dir / "song-one.psarc").read_bytes() == b"different"
    assert (target_dir / "song-one-2.psarc").read_bytes() == b"package-one"


def test_sync_surfaces_package_download_errors(tmp_path):
    provider = FakeProvider(tmp_path)

    def missing_package(path: str, params: dict | None = None):
        if path.endswith("/package"):
            raise RuntimeError('{"detail":"package not found"}')
        raise AssertionError(path)

    provider._bytes = missing_package

    with pytest.raises(RuntimeError, match="package not found"):
        provider.sync_song("song-one")