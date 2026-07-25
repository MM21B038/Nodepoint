# Nodepoint

Django backend for workspace-scoped documents, knowledge graphs (Postgres + Qdrant), and streaming agent chat over WebSocket.

## Stack

| Service | Role |
|---------|------|
| **Django + DRF** | REST API |
| **Channels + Redis** | WebSocket chat |
| **Postgres** | Workspaces, documents, KG entities/relations, chat, users |
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

## Authentication and roles

All `/api/` routes except registration and token exchange require authentication.

| Method | Header |
|--------|--------|
| JWT (interactive) | `Authorization: Bearer <access_token>` from `POST /api/auth/token/` |
| API key (automation) | `Authorization: Api-Key np_<prefix>_<secret>` |

**Roles**

| Role | Access |
|------|--------|
| **superadmin** | All users and resources; platform usage; creates admins |
| **admin** | Own data + managed users they created |
| **user** | Own workspaces, documents, groups, chat |

Public signup (`POST /api/auth/register/`) can create **`user`** or **`admin`** accounts only — not superadmin. Admins create managed users via `POST /api/auth/users/`.

Workspace and group **names are unique per owner**. When admin/superadmin sees duplicate names, pass `?owner_id=` or `?owner_username=` on name-based routes, or use `GET /api/workspace/lookup/` / `GET /api/group/lookup/` to resolve owners first. See [docs/API.md](docs/API.md#workspace--per-owner-naming).

## Create a superadmin (bootstrap)

The first superadmin must be created with a management command (not via the register API).

**Docker (interactive password prompt)**

```bash
docker compose exec web /app/.venv/bin/python manage.py createsuperadmin --username admin
```

**Docker (non-interactive — CI / scripts)**

```bash
SUPERADMIN_PASSWORD='YourSecurePass123!' \
  docker compose exec web /app/.venv/bin/python manage.py createsuperadmin \
  --username admin --noinput
```

Optional: `--email you@example.com`

**Local (uv)**

```bash
uv run python manage.py createsuperadmin --username admin
```

Then log in and obtain a JWT:

```bash
curl -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "YourSecurePass123!"}'
```

Use the `access` token on subsequent requests: `-H "Authorization: Bearer <access>"`.

> **Note:** Migration `0015` creates a internal `system` superuser for bootstrapping legacy data ownership. Do not use it for day-to-day login — create your own superadmin as above.

## Features

- **Workspaces** — isolated document + KG + chat scope (per-owner naming)
- **Workspace groups** — typed collections (`workspace`, `files`, `entity`, `relation`) for cross-workspace KG, search, and group chat; membership ownership rules for admin aggregate groups
- **Documents** — upload `.txt` / `.md`; RQ pipeline builds KG and vectors
- **Knowledge graph** — REST + chat agent tool `Knowledge.search_graph`
- **Chat** — multi-session REST + WebSocket per workspace or group; incognito mode
- **Auth** — JWT, scoped API keys, role-based visibility, account lifecycle

## Documentation

| Doc | Contents |
|-----|----------|
| [**docs/API.md**](docs/API.md) | Full REST + WebSocket reference, auth, scopes, examples |
| [docs/chat-websocket.md](docs/chat-websocket.md) | WebSocket protocol and compression |
| [docs/workspace-api.md](docs/workspace-api.md) | KG, groups, chat summary endpoints |

## Example flow

```bash
# 0. Bootstrap superadmin (once) — see section above, then:
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourSecurePass123!"}' | jq -r .access)
AUTH="Authorization: Bearer $TOKEN"

# 1. Create workspace and group
curl -X POST http://localhost:8000/api/workspace/create/ \
  -H "Content-Type: application/json" -H "$AUTH" \
  -d '{"name": "main"}'
curl -X POST http://localhost:8000/api/group/create/ \
  -H "Content-Type: application/json" -H "$AUTH" \
  -d '{"name": "research"}'
curl -X POST http://localhost:8000/api/group/research/workspaces/ \
  -H "Content-Type: application/json" -H "$AUTH" \
  -d '{"workspace_name": "main"}'

# 2. Upload (workspace_name optional → oldest workspace)
curl -X POST http://localhost:8000/api/document/upload/ \
  -H "$AUTH" -F file=@notes.md

# 3. Chat sessions (legacy GET /api/chat/... returns 400 — use sessions API)
curl -H "$AUTH" "http://localhost:8000/api/chat/group/research/sessions/"
# WebSocket: ws://localhost:8000/ws/chat/group/research/?session_id=<uuid>
# Per-workspace: /api/chat/main/sessions/  |  ws://localhost:8000/ws/chat/main/
```

## Project layout

```text
config/           Django settings, ASGI
nodepoint/        App: models, views, services, agent, auth, registry tools
docker/           entrypoint.sh
docs/             API reference and protocol notes
settings.toml     Shared service config
docker-compose.yml
```

## Tests

**Local**

```bash
uv run python manage.py test nodepoint
```

**Docker**

```bash
docker compose run --rm web /app/.venv/bin/python manage.py test nodepoint
```
