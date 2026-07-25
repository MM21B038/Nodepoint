# Chat WebSocket API

Backend-only streaming chat over Django Channels. Requires **ASGI** (`uvicorn config.asgi:application`) and **Redis** for the channel layer.

## Authentication

WebSocket connections require a valid JWT access token:

- Header: `Authorization: Bearer <access_token>` (preferred)
- Query: `?token=<access_token>` (alternative)

Missing or invalid tokens close the connection with code **4401**. The user must have access to the target workspace or group (same rules as REST).

## Quick start (saved session)

1. Obtain a token: `POST /api/auth/token/`
2. Create a session: `POST /api/chat/PRAJNA/sessions/` → `session_id`
3. Load history (optional): `GET /api/chat/PRAJNA/sessions/<session_id>/`
4. Connect: `ws://localhost:8000/ws/chat/PRAJNA/?session_id=<session_id>` with `Authorization: Bearer ...`
4. Send `{ "type": "chat.send", "content": "Hello" }`

## Incognito mode

Connect with `?incognito=true` (no `session_id`). Messages are **not** saved to Postgres. `chat.ready` includes `"incognito": true`. Partial cancel payloads use `saved` with `content` only (no `message_id`).

```
ws://localhost:8000/ws/chat/PRAJNA/?incognito=true
```

## Group-scoped chat

Same pattern under `/api/chat/group/<name>/sessions/` and `/ws/chat/group/<name>/?session_id=<uuid>`.

## REST session endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/chat/<workspace>/sessions/` | List sessions |
| POST | `/api/chat/<workspace>/sessions/` | Create session |
| GET | `/api/chat/<workspace>/sessions/<uuid>/` | History |
| PATCH | `/api/chat/<workspace>/sessions/<uuid>/` | Rename |
| DELETE | `/api/chat/<workspace>/sessions/<uuid>/` | Delete |
| POST | `/api/chat/<workspace>/sessions/<uuid>/clear/` | Clear messages |

Group paths: `/api/chat/group/<name>/sessions/...`

Legacy `GET/DELETE /api/chat/<workspace>/` return **`400`** (`session_id required`).

When multiple workspaces or groups share the same name (superadmin/admin view), add **`owner_id`** or **`owner_username`** to the connect URL — same rules as REST. If ambiguous, the server sends `{"type":"error","message":"...","candidates":[...]}` then closes.

```
ws://localhost:8000/ws/chat/123/?session_id=<uuid>&owner_id=3
ws://localhost:8000/ws/chat/group/team/?session_id=<uuid>&owner_id=3
```

## WebSocket connect query

| Query | Required | Description |
|-------|----------|-------------|
| `session_id` | One of `session_id` or `incognito` | UUID of a saved session |
| `incognito` | One of `session_id` or `incognito` | `true` / `1` for ephemeral chat |
| `token` | If not using Bearer header | JWT access token |

## WebSocket protocol

| URL | Scope |
|-----|--------|
| `/ws/chat/group/<name>/` | Group chat |
| `/ws/chat/<workspace_name>/` | Single workspace |

### Client → server

```json
{ "type": "chat.send", "content": "...", "exclude_servers": ["WikiServer"] }
{ "type": "chat.reconnect" }
{ "type": "chat.cancel" }
{ "type": "chat.status" }
{ "type": "ping" }
```

### Server → client

- `chat.ready` — on connect (`session_id`, `conversation_id` alias, `active_branch_id` when persisted, `incognito`, **`agent_busy`**)
- `chat.queued` — global parallel cap reached; this `chat.send` is waiting for a slot
- `chat.turn_started` — turn accepted (`turn_id`)
- `chat.reconnected` — reply to `chat.reconnect`
- `chat.status` — reply to `chat.status`
- `chat.branch_updated` — active branch changed after compression (persisted sessions only)
- Agent stream events — see `nodepoint/agent/schema.py`
- `chat.compress_*` / `chat.compressed` — persisted sessions only
- `chat.done` — turn finished
- `chat.interrupted` / `chat.cancelled` — optional `saved` (`message_id` when persisted)

### Stop streaming (`chat.cancel`)

Send `{ "type": "chat.cancel" }`. Persisted sessions: partial text saved to Postgres. Incognito: `saved` contains inline `content` only.

### Disconnect and reconnect

Reconnect with the **same** `session_id` (or same ephemeral id if you stored it from `chat.ready`) to attach to a running turn. Refresh history via `GET .../sessions/<session_id>/` after offline gaps.

## Search scope

| Chat mode | `Knowledge.search_graph` searches |
|-----------|----------------------------------|
| Group | Scoped by group `tag` |
| Per-workspace | That workspace only |

## Concurrency

| Limit | Scope | Default |
|-------|--------|---------|
| One turn | Per `session_id` | — |
| `CHAT_MAX_CONCURRENT_TURNS` | All sessions, workspaces, group chats (Redis, all `web` workers) | `8` |
| `CHAT_MAX_CONCURRENT_SEARCHES` | Knowledge tool calls per `web` worker | `8` |

Open multiple WebSocket connections (different `session_id` or workspaces) to chat in parallel until the global turn cap is reached. Additional `chat.send` calls **queue** on the same socket: you receive `chat.queued`, then `chat.turn_started` when a slot frees up. Cancel with `chat.cancel` while waiting.

Preprocess/embed jobs use the RQ **`worker`** service and are not counted toward chat turn slots.

| Event | When |
|-------|------|
| `chat.queued` | All parallel slots busy; request is waiting |
| `chat.turn_started` | Slot acquired; agent run begins |
| `chat_queue_timeout` error | Wait exceeded `CHAT_TURN_QUEUE_TIMEOUT` (default 300s) |

## Environment

See [API.md](API.md) Chat section for `CHAT_*`, `WEB_WORKERS`, and related settings.
