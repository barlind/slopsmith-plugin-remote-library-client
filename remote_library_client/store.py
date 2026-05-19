from __future__ import annotations

import json
from pathlib import Path
from threading import RLock


class RemoteLibraryClientStore:
    def __init__(self, config_dir: Path) -> None:
        self.root = Path(config_dir) / "remote_library_client"
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.root / "settings.json"
        self._lock = RLock()

    def load(self) -> dict:
        if not self.settings_path.exists():
            return {"sources": []}
        try:
            data = json.loads(self.settings_path.read_text())
        except json.JSONDecodeError:
            return {"sources": []}
        if not isinstance(data, dict):
            return {"sources": []}
        sources = data.get("sources") if isinstance(data.get("sources"), list) else []
        return {"sources": [item for item in sources if isinstance(item, dict)]}

    def save(self, data: dict) -> dict:
        normalized = {"sources": list(data.get("sources") or [])}
        with self._lock:
            self.settings_path.write_text(json.dumps(normalized, indent=2, sort_keys=True))
        return normalized

    def list_sources(self) -> list[dict]:
        return list(self.load().get("sources") or [])

    def upsert_source(self, source: dict) -> dict:
        sources = [item for item in self.list_sources() if item.get("providerId") != source.get("providerId")]
        sources.append(source)
        self.save({"sources": sources})
        return source

    def remove_source(self, provider_id: str) -> bool:
        sources = self.list_sources()
        remaining = [item for item in sources if item.get("providerId") != provider_id]
        self.save({"sources": remaining})
        return len(remaining) != len(sources)