# Chat WebSocket API

Backend-only streaming chat over Django Channels. Requires **ASGI** (`uvicorn config.asgi:application`) and **Redis** for the channel layer.

## Quick start (flagged-scope / “global” chat)

1. `GET /api/chat/?flagged=true` — separate chat thread (no workspace setup required)
2. Connect: `ws://localhost:8000/ws/chat/flagged/`
3. Star workspaces (`PATCH .../toggle-flag/`) so `Knowledge.search_graph` can search their KG
4. Send `{ "type": "chat.send", "content": "Hello" }`

## Per-workspace chat

`GET /api/chat/<workspace_name>/` and `ws://localhost:8000/ws/chat/<workspace_name>/` — separate thread for that workspace only.

## REST endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/chat/?flagged=true` | Flagged-scope chat history |
| DELETE | `/api/chat/?flagged=true` | Clear flagged-scope chat |
| GET | `/api/chat/<workspace_name>/` | Named workspace chat |
| DELETE | `/api/chat/<workspace_name>/` | Clear named workspace chat |

## WebSocket protocol

| URL | Scope |
|-----|--------|
| `/ws/chat/flagged/` | Flagged-scope chat (`chat.ready` includes `flagged`, `starred_workspaces`) |
| `/ws/chat/<workspace_name>/` | Single workspace (`chat.ready` includes `workspace`) |

### Client → server

```json
{ "type": "chat.send", "content": "...", "exclude_servers": ["WikiServer"] }
{ "type": "chat.cancel" }
{ "type": "ping" }
```

### Server → client

- `chat.ready` — on connect
- Agent stream events — see `nodepoint/agent/schema.py`
- `chat.compressed` — internal context compression
- `chat.done` — turn finished

## Search scope

| Chat mode | `Knowledge.search_graph` searches |
|-----------|----------------------------------|
| Flagged (`?flagged=true` / `ws/chat/flagged/`) | User-starred workspaces only |
| Per-workspace | That workspace + all starred |

If none are starred during flagged chat, the tool returns: *No workspace is flagged…*

## Environment

- `CHAT_COMPRESS_TOKEN_THRESHOLD` — default `64000`
- `CHAT_DEFAULT_SYSTEM` — system prompt for new chats
- `BASE_URL`, `API_KEY` — required for `Agent`
