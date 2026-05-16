# Nodepoint

Django backend for workspace-scoped documents, knowledge graphs (Postgres + Qdrant), and streaming agent chat over WebSocket.

## Stack

| Service | Role |
|---------|------|
| **Django + DRF** | REST API |
| **Channels + Redis** | WebSocket chat |
| **Postgres** | Workspaces, documents, KG entities/relations, chat |
| **MongoDB** | Auxiliary document store |
| **Qdrant** | Vector search for knowledge |
| **RQ + Redis** | Background document preprocessing |

## Quick start (Docker)

```bash
cp .env.example .env   # set BASE_URL, API_KEY for the agent
docker compose up -d --build
```

| Service | Port |
|---------|------|
| Web | 8000 |
| Postgres | 5432 |
| Mongo | 27017 |
| Redis | 6379 |
| Qdrant | 6333 |

Migrations run on web container start (`RUN_MIGRATIONS=1`).

## Features

- **Workspaces** — isolated document + KG + chat scope; **star** (`is_flag`) for shared search and default upload/chat alias
- **Documents** — upload `.txt` / `.md`; RQ pipeline builds KG and vectors
- **Knowledge graph** — REST + chat agent tool `Knowledge.search_graph` (resolves hits to sourced markdown)
- **Chat** — one thread per workspace; `default` alias → first starred workspace; WebSocket token streaming + internal compression

## Documentation

| Doc | Contents |
|-----|----------|
| [**API.md**](API.md) | Full REST + WebSocket reference, event types, examples |
| [docs/chat-websocket.md](docs/chat-websocket.md) | WebSocket protocol and compression |
| [docs/workspace-api.md](docs/workspace-api.md) | KG + chat summary endpoints |

## Example flow

```bash
# 1. Create workspace and star it
curl -X POST http://localhost:8000/api/workspace/create/ \
  -H "Content-Type: application/json" -d '{"name": "main"}'
curl -X PATCH http://localhost:8000/api/workspace/main/toggle-flag/

# 2. Upload (workspace_name optional → first starred)
curl -X POST http://localhost:8000/api/document/upload/ \
  -F file=@notes.md

# 3. Flagged-scope chat (separate from workspace "main")
curl "http://localhost:8000/api/chat/?flagged=true"
# WebSocket: ws://localhost:8000/ws/chat/flagged/
# Per-workspace: GET /api/chat/main/  |  ws://localhost:8000/ws/chat/main/
```

## Project layout

```text
config/           Django settings, ASGI
nodepoint/        App: models, views, services, agent, registry tools
docker/           entrypoint.sh
docs/             Additional API notes
API.md            API reference (root)
settings.toml     Shared service config
docker-compose.yml
```

## Tests

```bash
uv run python manage.py test nodepoint
```
