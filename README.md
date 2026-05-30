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

Migrations run on web container start (`RUN_MIGRATIONS=1`). After model changes, generate migration files with `python manage.py makemigrations` and commit them — they apply automatically on the next `docker compose up`.

## Features

- **Workspaces** — isolated document + KG + chat scope
- **Workspace groups** — named sets of workspaces for cross-workspace KG, search, and group chat
- **Documents** — upload `.txt` / `.md`; RQ pipeline builds KG and vectors
- **Knowledge graph** — REST + chat agent tool `Knowledge.search_graph` (resolves hits to sourced markdown)
- **Chat** — one thread per workspace or per group; WebSocket token streaming + internal compression

## Documentation

| Doc | Contents |
|-----|----------|
| [**API.md**](API.md) | Full REST + WebSocket reference, event types, examples |
| [docs/chat-websocket.md](docs/chat-websocket.md) | WebSocket protocol and compression |
| [docs/workspace-api.md](docs/workspace-api.md) | KG + chat summary endpoints |

## Example flow

```bash
# 1. Create workspace and group
curl -X POST http://localhost:8000/api/workspace/create/ \
  -H "Content-Type: application/json" -d '{"name": "main"}'
curl -X POST http://localhost:8000/api/group/create/ \
  -H "Content-Type: application/json" -d '{"name": "research"}'
curl -X POST http://localhost:8000/api/group/research/workspaces/ \
  -H "Content-Type: application/json" -d '{"workspace_name": "main"}'

# 2. Upload (workspace_name optional → oldest workspace)
curl -X POST http://localhost:8000/api/document/upload/ \
  -F file=@notes.md

# 3. Group chat (separate from workspace "main")
curl "http://localhost:8000/api/chat/group/research/"
# WebSocket: ws://localhost:8000/ws/chat/group/research/
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
