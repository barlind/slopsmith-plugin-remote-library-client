from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib import error, parse, request

from fastapi.responses import Response

LibraryImporter = Callable[[Path, Path], dict | None]


def provider_id_for_source(source_id: str, base_url: str) -> str:
    raw = source_id or base_url
    slug = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-_.:")[:80]
    digest = hashlib.sha1(base_url.encode("utf-8")).hexdigest()[:10]
    return f"direct:{slug or 'source'}:{digest}"


def sanitize_filename(value: str, fallback: str = "remote-song") -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", value).strip(" ._")
    return name or fallback


def safe_path_segment(value: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-_")
    return (cleaned[:80].rstrip(" .-_") or fallback)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DirectLibraryProvider:
    kind = "remote"
    capabilities = ("library.read", "art.read", "song.sync")
    metadata_cache_ttl_seconds = 300
    metadata_cache_max_entries = 256

    def __init__(
        self,
        source: dict,
        cache_dir: Path,
        local_library_root: Path | None = None,
        library_importer: LibraryImporter | None = None,
    ) -> None:
        self.source = dict(source)
        self.base_url = str(source.get("baseUrl") or "").rstrip("/")
        self.id = str(source.get("providerId") or provider_id_for_source(source.get("sourceId") or "", self.base_url))
        self.label = str(source.get("label") or source.get("sourceName") or self.base_url)
        self.cache_dir = Path(cache_dir) / sanitize_filename(self.id.replace(":", "_"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.local_library_root = Path(local_library_root) if local_library_root else None
        self.library_importer = library_importer
        self._metadata_cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, dict]] = {}
        self._metadata_cache_lock = threading.RLock()

    def _url(self, path: str, params: dict | None = None) -> str:
        query = f"?{parse.urlencode(params)}" if params else ""
        return f"{self.base_url}{path}{query}"

    def _json(self, path: str, params: dict | None = None, timeout: float = 20) -> dict:
        req = request.Request(self._url(path, params), headers={"ngrok-skip-browser-warning": "true"})
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise RuntimeError(detail or str(exc)) from exc

    def _metadata_cache_key(self, path: str, params: dict | None = None) -> tuple[str, tuple[tuple[str, str], ...]]:
        normalized_params = tuple(sorted((str(key), str(value)) for key, value in (params or {}).items()))
        return path, normalized_params

    def _json_cached(self, path: str, params: dict | None = None, timeout: float = 20) -> dict:
        key = self._metadata_cache_key(path, params)
        now = time.monotonic()
        with self._metadata_cache_lock:
            cached = self._metadata_cache.get(key)
            if cached and now - cached[0] <= self.metadata_cache_ttl_seconds:
                return copy.deepcopy(cached[1])
            if cached:
                self._metadata_cache.pop(key, None)

        payload = self._json(path, params, timeout=timeout)

        with self._metadata_cache_lock:
            if len(self._metadata_cache) >= self.metadata_cache_max_entries:
                oldest_key = min(self._metadata_cache, key=lambda item: self._metadata_cache[item][0])
                self._metadata_cache.pop(oldest_key, None)
            self._metadata_cache[key] = (now, copy.deepcopy(payload))
        return payload

    def clear_metadata_cache(self) -> None:
        with self._metadata_cache_lock:
            self._metadata_cache.clear()

    def _bytes(self, path: str, params: dict | None = None) -> tuple[bytes, str, dict]:
        req = request.Request(self._url(path, params), headers={"ngrok-skip-browser-warning": "true"})
        try:
            with request.urlopen(req, timeout=120) as response:
                return (
                    response.read(),
                    response.headers.get("content-type") or "application/octet-stream",
                    dict(response.headers),
                )
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise RuntimeError(detail or str(exc)) from exc

    def _art_cache_paths(self, song_id: str) -> tuple[Path, Path]:
        art_dir = self.cache_dir / "art"
        art_dir.mkdir(parents=True, exist_ok=True)
        safe_id = sanitize_filename(song_id, "remote-art")
        return art_dir / f"{safe_id}.bin", art_dir / f"{safe_id}.json"

    def _read_cached_art(self, song_id: str) -> tuple[bytes, str] | None:
        content_path, metadata_path = self._art_cache_paths(song_id)
        if not content_path.exists():
            return None
        media_type = "image/png"
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text())
                media_type = str(metadata.get("mediaType") or media_type)
            except Exception:
                pass
        try:
            return content_path.read_bytes(), media_type
        except OSError:
            return None

    def _write_cached_art(self, song_id: str, content: bytes, media_type: str) -> None:
        content_path, metadata_path = self._art_cache_paths(song_id)
        try:
            content_path.write_bytes(content)
            metadata_path.write_text(json.dumps({"mediaType": media_type}))
        except OSError:
            pass

    def _source_folder_name(self) -> str:
        return safe_path_segment(self.source.get("sourceId") or self.label or self.id, "remote-source")

    def _library_target(self, filename: str, content_hash: str) -> tuple[Path, str] | None:
        if not self.local_library_root or not self.local_library_root.exists() or not self.local_library_root.is_dir():
            return None
        target_dir = self.local_library_root / self._source_folder_name()
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = sanitize_filename(Path(filename).name, "remote-song.psarc")
        target = target_dir / safe_name
        if target.exists() and _sha256_file(target) == content_hash:
            return target, target.relative_to(self.local_library_root).as_posix()
        stem = target.stem or "remote-song"
        suffix = target.suffix or ".psarc"
        for index in range(1, 1000):
            candidate = target if index == 1 else target_dir / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate, candidate.relative_to(self.local_library_root).as_posix()
            if _sha256_file(candidate) == content_hash:
                return candidate, candidate.relative_to(self.local_library_root).as_posix()
        raise RuntimeError("unable to allocate a unique local library filename")

    def _write_atomic(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp_path.write_bytes(content)
            tmp_path.replace(target)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _remote_query_params(
        self,
        *,
        page: int,
        size: int,
        sort: str,
        direction: str,
        q: str = "",
        **kwargs,
    ) -> dict:
        params = {
            "q": q or "",
            "page": max(0, int(page or 0)),
            "pageSize": max(1, min(100, int(size or 24))),
            "sort": sort or "artist",
            "direction": direction or "asc",
        }
        if kwargs.get("format_filter"):
            params["format"] = kwargs["format_filter"]
        for key in ("arrangements_has", "arrangements_lacks", "stems_has", "stems_lacks", "tunings"):
            values = [str(value) for value in (kwargs.get(key) or []) if value]
            if values:
                params[key] = ",".join(values)
        has_lyrics = kwargs.get("has_lyrics")
        if has_lyrics is not None:
            params["has_lyrics"] = str(int(bool(has_lyrics)))
        return params

    def _normalize_song(self, song: dict) -> dict:
        remote_id = str(song.get("remoteSongId") or song.get("songId") or song.get("id") or "")
        title = song.get("title") or remote_id or "Remote song"
        package_form = song.get("packageForm") or ""
        song_format = song.get("format") or ("sloppak" if "sloppak" in package_form else "psarc")
        stem_ids = list(song.get("stem_ids") or song.get("stemIds") or [])
        stem_count = song.get("stem_count", song.get("stemCount"))
        if stem_count is None:
            stem_count = len(stem_ids)
        return {
            **song,
            "filename": remote_id,
            "song_id": remote_id,
            "remote_id": remote_id,
            "remoteSongId": remote_id,
            "libraryProviderId": self.id,
            "provider": self.id,
            "sourceId": song.get("sourceId") or self.source.get("sourceId"),
            "sourceName": self.label,
            "title": title,
            "artist": song.get("artist") or "Unknown artist",
            "album": song.get("album") or "",
            "format": song_format,
            "stem_count": int(stem_count or 0),
            "stem_ids": stem_ids,
            "localFilename": "",
            "local_filename": "",
            "playFilename": "",
            "arrangements": list(song.get("arrangements") or []),
            "has_lyrics": bool(song.get("has_lyrics") or song.get("hasLyrics")),
            "tuning": song.get("tuning") or song.get("tuningName") or song.get("tuning_name") or "",
            "tuning_name": song.get("tuning_name") or song.get("tuningName") or song.get("tuning") or "",
            "sizeBytes": song.get("sizeBytes") or song.get("size_bytes") or 0,
        }

    def _normalize_artist_payload(self, artists: list[dict]) -> list[dict]:
        normalized_artists = []
        for artist in artists:
            albums = []
            for album in artist.get("albums") or []:
                songs = [self._normalize_song(song) for song in album.get("songs") or []]
                albums.append({**album, "songs": songs})
            normalized_artists.append({**artist, "albums": albums})
        return normalized_artists

    def query_page(self, page: int = 0, size: int = 24, sort: str = "artist", direction: str = "asc", **kwargs):
        if kwargs.get("favorites_only"):
            return [], 0
        payload = self._json_cached(
            "/songs",
            self._remote_query_params(page=page, size=size, sort=sort, direction=direction, **kwargs),
        )
        songs = [self._normalize_song(song) for song in payload.get("songs") or []]
        return songs, int(payload.get("total") or len(songs))

    def query_artists(self, letter: str = "", page: int = 0, size: int = 50, **kwargs):
        if kwargs.get("favorites_only"):
            return [], 0
        params = self._remote_query_params(page=page, size=size, sort="artist", direction="asc", **kwargs)
        if letter:
            params["letter"] = letter
        payload = self._json_cached("/artists", params)
        return self._normalize_artist_payload(payload.get("artists") or []), int(payload.get("total_artists") or 0)

    def query_stats(self, **kwargs) -> dict:
        if kwargs.get("favorites_only"):
            return {"total_songs": 0, "total_artists": 0, "letters": {}}
        payload = self._json_cached(
            "/stats",
            self._remote_query_params(page=0, size=1, sort="artist", direction="asc", **kwargs),
        )
        return {
            "total_songs": int(payload.get("total_songs") or 0),
            "total_artists": int(payload.get("total_artists") or 0),
            "letters": dict(payload.get("letters") or {}),
        }

    def tuning_names(self) -> dict:
        payload = self._json_cached("/tuning-names")
        tunings = payload.get("tunings")
        return {"tunings": tunings if isinstance(tunings, list) else []}

    def get_art(self, song_id: str):
        cached = self._read_cached_art(song_id)
        if cached:
            content, media_type = cached
            return Response(content=content, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
        try:
            content, media_type, _headers = self._bytes(f"/songs/{parse.quote(song_id)}/art")
        except RuntimeError as exc:
            if "404" in str(exc) or "artwork not found" in str(exc) or "song not found" in str(exc):
                return None
            raise
        self._write_cached_art(song_id, content, media_type)
        return Response(content=content, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})

    def sync_song(self, song_id: str) -> dict:
        content, _media_type, headers = self._bytes(f"/songs/{parse.quote(song_id)}/package")
        disposition = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
        filename = ""
        match = re.search(r'filename="?([^";]+)"?', disposition)
        if match:
            filename = match.group(1)
        if not filename:
            filename = sanitize_filename(song_id) + ".psarc"
        content_hash = _sha256_bytes(content)
        target = self.cache_dir / sanitize_filename(filename)
        self._write_atomic(target, content)
        library_target = self._library_target(filename, content_hash)
        library_path = None
        local_filename = ""
        library_import_result = None
        library_import_error = ""
        if library_target:
            library_path, local_filename = library_target
            wrote_library = False
            if not library_path.exists() or _sha256_file(library_path) != content_hash:
                self._write_atomic(library_path, content)
                wrote_library = True
            if self.library_importer and self.local_library_root:
                try:
                    library_import_result = self.library_importer(library_path, self.local_library_root)
                except Exception as exc:
                    library_import_error = str(exc)
                    if wrote_library:
                        try:
                            library_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    library_path = None
                    local_filename = ""
        self.clear_metadata_cache()
        result = {
            "ok": True,
            "song_id": song_id,
            "remoteSongId": song_id,
            "cachedPath": str(target),
            "bytes": len(content),
        }
        if library_path and local_filename:
            result.update({
                "filename": local_filename,
                "localFilename": local_filename,
                "local_filename": local_filename,
                "playFilename": local_filename,
                "libraryPath": str(library_path),
                "libraryRelativePath": local_filename,
                "libraryImportState": "indexed" if library_import_result else "staged",
                "playbackSource": "library-folder",
            })
            if library_import_result:
                result.update(library_import_result)
        else:
            result["playbackSource"] = "remote-cache"
            if library_import_error:
                result["libraryImportState"] = "failed"
                result["libraryImportError"] = library_import_error
        return result