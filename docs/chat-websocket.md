# Chat WebSocket API

Backend-only streaming chat over Django Channels. Requires **ASGI** (`uvicorn config.asgi:application`) and **Redis** for the channel layer.

## Quick start (group-scoped chat)

1. Create a group: `POST /api/group/create/` with `{ "name": "research" }`
2. Add workspaces: `POST /api/group/research/workspaces/` with `{ "workspace_name": "..." }`
3. `GET /api/chat/group/research/` — lazy-create group chat thread
4. Connect: `ws://localhost:8000/ws/chat/group/research/`
5. Send `{ "type": "chat.send", "content": "Hello" }`

## Per-workspace chat

`GET /api/chat/<workspace_name>/` and `ws://localhost:8000/ws/chat/<workspace_name>/` — separate thread for that workspace only.

## REST endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/chat/group/<name>/` | Group-scoped chat history |
| DELETE | `/api/chat/group/<name>/` | Clear group-scoped chat |
| GET | `/api/chat/<workspace_name>/` | Named workspace chat |
| DELETE | `/api/chat/<workspace_name>/` | Clear named workspace chat |

## WebSocket protocol

| URL | Scope |
|-----|--------|
| `/ws/chat/group/<name>/` | Group chat (`chat.ready` includes `group`, `workspaces`) |
| `/ws/chat/<workspace_name>/` | Single workspace (`chat.ready` includes `workspace`) |

### Client → server

```json
{ "type": "chat.send", "content": "...", "exclude_servers": ["WikiServer"] }
{ "type": "chat.reconnect" }
{ "type": "chat.cancel" }
{ "type": "chat.status" }
{ "type": "ping" }
```

### Server → client

- `chat.ready` — on connect (`conversation_id`, `active_branch_id`; `agent_busy: true` if a turn is still running — live stream auto-attaches)
- `chat.turn_started` — turn accepted (`turn_id`)
- `chat.reconnected` — reply to `chat.reconnect`
- `chat.status` — reply to `chat.status` (`agent_busy`, `turn_id`, `turn_started_at`)
- `chat.branch_updated` — active branch changed after compression
- Agent stream events — see `nodepoint/agent/schema.py` (via channel-layer fan-out)
- `chat.compressed` — internal context compression
- `chat.done` — turn finished (`turn_id`, optional `active_branch_id`)
- `chat.interrupted` — partial assistant text was saved (disconnect/cancel mid-stream)
- `chat.cancelled` — turn stopped by `chat.cancel`

### Disconnect and reconnect (live stream)

If the WebSocket drops **during** a turn:

- The agent **keeps running** (not cancelled).
- Events are broadcast on the conversation channel group (`chat_{conversation_id}`), so **any** reconnect to the same workspace/group chat receives **live** tokens from that point on.
- Partial assistant text is still **flushed to Postgres** on cancel/disconnect mid-stream.

After reconnect:

1. Open WebSocket again (or send `{ "type": "chat.reconnect" }` on an existing socket).
2. On `chat.ready` with `agent_busy: true`, you are already subscribed — live events flow immediately.
3. **Once**, call `GET /api/chat/<workspace>/` or `GET /api/chat/group/<name>/` to fill text that arrived while you were offline (no token replay).
4. Send `chat.send` only when `agent_busy` is false.

Use `ping` / `pong` for keepalive on long tool or LLM runs (`AGENT_REQUEST_TIMEOUT` defaults to 300s).

Multiple tabs on the same chat each receive the same live stream.

## Search scope

| Chat mode | `Knowledge.search_graph` searches |
|-----------|----------------------------------|
| Group (`/ws/chat/group/<name>/`) | All workspaces in that group |
| Flagged (`/ws/chat/flagged/`) | Workspaces in group `flagged` |
| Per-workspace | That workspace only |

If a group has no member workspaces, the tool returns a message that the group is empty.

## Concurrency

- Each WebSocket connection runs **one agent task** at a time (`Agent busy` if a second `chat.send` arrives during a turn).
- Different users/workspaces run **in parallel** on the same `web` service (async event loop + httpx LLM streaming).
- Knowledge tools share a per-worker slot limit: `CHAT_MAX_CONCURRENT_SEARCHES` (default `8`).
- Scale horizontally: set `WEB_WORKERS` (default `4`) on the `web` container, or `docker compose up --scale web=2`.
- Document preprocess/embed jobs stay on the separate `worker` RQ service (not used for live chat).

## Environment

- `CHAT_COMPRESS_TOKEN_THRESHOLD` — default `64000`
- `CHAT_DEFAULT_SYSTEM` — system prompt for new chats
- `CHAT_MAX_CONCURRENT_SEARCHES` — default `8`
- `WEB_WORKERS` — uvicorn worker processes (default `4`)
- `DB_CONN_MAX_AGE` — Postgres connection reuse per worker (default `60`)
- `AGENT_REQUEST_TIMEOUT` — LLM HTTP read timeout seconds (default `300`)
- `BASE_URL`, `API_KEY` — required for `Agent`
