from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib import error, parse, request

from fastapi.responses import Response


def provider_id_for_source(source_id: str, base_url: str) -> str:
    raw = source_id or base_url
    slug = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-_.:")[:80]
    digest = hashlib.sha1(base_url.encode("utf-8")).hexdigest()[:10]
    return f"direct:{slug or 'source'}:{digest}"


def sanitize_filename(value: str, fallback: str = "remote-song") -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", value).strip(" ._")
    return name or fallback


class DirectLibraryProvider:
    kind = "remote"
    capabilities = ("library.read", "art.read", "song.sync")

    def __init__(self, source: dict, cache_dir: Path) -> None:
        self.source = dict(source)
        self.base_url = str(source.get("baseUrl") or "").rstrip("/")
        self.id = str(source.get("providerId") or provider_id_for_source(source.get("sourceId") or "", self.base_url))
        self.label = str(source.get("label") or source.get("sourceName") or self.base_url)
        self.cache_dir = Path(cache_dir) / sanitize_filename(self.id.replace(":", "_"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _url(self, path: str, params: dict | None = None) -> str:
        query = f"?{parse.urlencode(params)}" if params else ""
        return f"{self.base_url}{path}{query}"

    def _json(self, path: str, params: dict | None = None) -> dict:
        req = request.Request(self._url(path, params), headers={"ngrok-skip-browser-warning": "true"})
        try:
            with request.urlopen(req, timeout=20) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise RuntimeError(detail or str(exc)) from exc

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

    def _all_songs(self, q: str = "") -> list[dict]:
        songs: list[dict] = []
        cursor = None
        while True:
            params = {"q": q or "", "pageSize": 500}
            if cursor:
                params["cursor"] = cursor
            payload = self._json("/songs", params)
            songs.extend(self._normalize_song(song) for song in payload.get("songs") or [])
            cursor = payload.get("nextCursor")
            if not cursor:
                return songs

    def _normalize_song(self, song: dict) -> dict:
        remote_id = str(song.get("remoteSongId") or song.get("songId") or song.get("id") or "")
        title = song.get("title") or remote_id or "Remote song"
        package_form = song.get("packageForm") or ""
        song_format = song.get("format") or ("sloppak" if "sloppak" in package_form else "psarc")
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
            "arrangements": list(song.get("arrangements") or []),
            "has_lyrics": bool(song.get("has_lyrics") or song.get("hasLyrics")),
            "tuning": song.get("tuning") or song.get("tuningName") or song.get("tuning_name") or "",
            "tuning_name": song.get("tuning_name") or song.get("tuningName") or song.get("tuning") or "",
            "sizeBytes": song.get("sizeBytes") or song.get("size_bytes") or 0,
        }

    def _filtered_songs(self, **kwargs) -> list[dict]:
        songs = self._all_songs(kwargs.get("q") or "")
        fmt = kwargs.get("format_filter") or ""
        if fmt:
            songs = [song for song in songs if str(song.get("format") or "") == fmt]
        if kwargs.get("favorites_only"):
            return []
        has_lyrics = kwargs.get("has_lyrics")
        if has_lyrics is not None:
            songs = [song for song in songs if bool(song.get("has_lyrics")) is bool(has_lyrics)]
        tunings = set(kwargs.get("tunings") or [])
        if tunings:
            songs = [song for song in songs if (song.get("tuning_name") or song.get("tuning")) in tunings]
        arrangements_has = set(kwargs.get("arrangements_has") or [])
        arrangements_lacks = set(kwargs.get("arrangements_lacks") or [])
        if arrangements_has or arrangements_lacks:
            def names(song: dict) -> set[str]:
                return {
                    str(item.get("name") or item.get("arrangement") or "")
                    for item in song.get("arrangements") or []
                }

            if arrangements_has:
                songs = [song for song in songs if arrangements_has.issubset(names(song))]
            if arrangements_lacks:
                songs = [song for song in songs if not arrangements_lacks.intersection(names(song))]
        return songs

    def query_page(self, page: int = 0, size: int = 24, sort: str = "artist", direction: str = "asc", **kwargs):
        songs = self._filtered_songs(**kwargs)
        sort_key = "title" if str(sort).startswith("title") else "artist"
        songs.sort(
            key=lambda song: (str(song.get(sort_key) or "").lower(), str(song.get("title") or "").lower()),
            reverse=direction == "desc" or str(sort).endswith("-desc"),
        )
        offset = max(0, int(page or 0)) * max(1, int(size or 24))
        return songs[offset:offset + size], len(songs)

    def query_artists(self, letter: str = "", page: int = 0, size: int = 50, **kwargs):
        songs = self._filtered_songs(**kwargs)
        grouped: dict[str, dict] = {}
        for song in songs:
            artist = song.get("artist") or "Unknown artist"
            album = song.get("album") or "Unknown Album"
            item = grouped.setdefault(artist, {"name": artist, "album_count": 0, "song_count": 0, "albums": {}})
            item["song_count"] += 1
            item["albums"].setdefault(album, {"name": album, "songs": []})["songs"].append(song)
        artists = []
        for artist in sorted(grouped.values(), key=lambda item: item["name"].lower()):
            first = artist["name"][:1].upper()
            artist_letter = first if first.isalpha() else "#"
            if letter and letter != artist_letter:
                continue
            albums = list(artist["albums"].values())
            artist["album_count"] = len(albums)
            artist["albums"] = albums
            artists.append(artist)
        offset = max(0, int(page or 0)) * max(1, int(size or 50))
        return artists[offset:offset + size], len(artists)

    def query_stats(self, **kwargs) -> dict:
        songs = self._filtered_songs(**kwargs)
        artists = {song.get("artist") or "Unknown artist" for song in songs}
        letters: dict[str, int] = {}
        for artist in artists:
            first = artist[:1].upper()
            letter = first if first.isalpha() else "#"
            letters[letter] = letters.get(letter, 0) + 1
        return {"total_songs": len(songs), "total_artists": len(artists), "letters": letters}

    def tuning_names(self) -> dict:
        counts: dict[str, int] = {}
        for song in self._all_songs(""):
            name = song.get("tuning_name") or song.get("tuning") or ""
            if name:
                counts[name] = counts.get(name, 0) + 1
        return {"tunings": [{"name": name, "sort_key": 0, "count": count} for name, count in sorted(counts.items())]}

    def get_art(self, song_id: str):
        content, media_type, _headers = self._bytes(f"/songs/{parse.quote(song_id)}/art")
        return Response(content=content, media_type=media_type)

    def sync_song(self, song_id: str) -> dict:
        song = next((item for item in self._all_songs("") if item.get("remoteSongId") == song_id), None)
        if not song:
            raise RuntimeError("remote song not found")
        package_hash = song.get("packageHash") or ""
        params = {"packageHash": package_hash} if package_hash else None
        content, _media_type, headers = self._bytes(f"/songs/{parse.quote(song_id)}/package", params)
        if package_hash.startswith("sha256:"):
            actual = "sha256:" + hashlib.sha256(content).hexdigest()
            if actual != package_hash:
                raise RuntimeError("downloaded package hash did not match remote metadata")
        disposition = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
        filename = ""
        match = re.search(r'filename="?([^";]+)"?', disposition)
        if match:
            filename = match.group(1)
        if not filename:
            suffix = ".sloppak" if "sloppak" in str(song.get("packageForm") or song.get("format") or "") else ".psarc"
            filename = sanitize_filename(f"{song.get('artist', '')} - {song.get('title', song_id)}") + suffix
        target = self.cache_dir / sanitize_filename(filename)
        target.write_bytes(content)
        return {
            "ok": True,
            "song_id": song_id,
            "remoteSongId": song_id,
            "cachedPath": str(target),
            "packageHash": package_hash,
            "bytes": len(content),
        }