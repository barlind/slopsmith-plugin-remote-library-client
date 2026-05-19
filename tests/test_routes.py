from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_add_source_registers_library_provider(tmp_path, monkeypatch):
    routes = importlib.import_module("routes")
    routes = importlib.reload(routes)
    registered = {}
    unregistered = []

    def probe(base_url: str) -> dict:
        assert base_url == "https://studio.example.test"
        return {
            "ok": True,
            "sourceId": "direct_studio",
            "sourceName": "Studio Source",
            "songCount": 12,
            "server": {"protocol": "slopsmith-direct-library.v1"},
        }

    monkeypatch.setattr(routes, "_probe_source", probe)
    app = FastAPI()
    routes.setup(app, {
        "config_dir": tmp_path / "config",
        "register_library_provider": lambda provider, replace=False: registered.setdefault(provider.id, provider),
        "unregister_library_provider": lambda provider_id: unregistered.append(provider_id),
        "get_sloppak_cache_dir": lambda: tmp_path / "cache",
    })
    client = TestClient(app)

    added = client.post("/api/plugins/remote_library_client/sources", json={
        "baseUrl": "https://studio.example.test/",
        "label": "Studio",
    })
    status = client.get("/api/plugins/remote_library_client/status")

    assert added.status_code == 200
    provider_id = added.json()["provider"]["id"]
    assert provider_id in registered
    assert status.json()["sources"][0]["providerId"] == provider_id
    assert status.json()["sources"][0]["online"] is True

    removed = client.delete(f"/api/plugins/remote_library_client/sources/{provider_id}")
    assert removed.status_code == 200
    assert provider_id in unregistered