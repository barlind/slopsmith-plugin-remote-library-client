# Slopsmith Remote Library Client

Remote Library Client connects Slopsmith to one or more direct Remote Library Server URLs. Each configured server is registered as a Slopsmith library provider, so it appears in the core Library source selector.

## Flow

```mermaid
flowchart LR
  UI[Slopsmith Library] --> Provider[Remote Library Client provider]
  Provider -->|GET /source| Server[Remote Library Server]
  Provider -->|GET /songs| Server
  Provider -->|GET /songs/{id}/art| Server
  Provider -->|GET /songs/{id}/package| Server
```

## Usage

1. Install the Remote Library Server plugin on the machine that owns the library.
2. Start that server on its own port.
3. Install this client plugin on the browsing machine.
4. Open **Remote Client** and add the server base URL, such as `http://127.0.0.1:8765` or an ngrok URL.
5. Open the main Library screen and choose the remote source from the source selector.

This first version has no relay and no pairing. Anyone with the direct server URL can access the exposed library API.

## Development

```bash
pytest
ruff check .
node --check screen.js
```