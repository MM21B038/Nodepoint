# Chat WebSocket API

Backend-only streaming chat over Django Channels. Requires **ASGI** (`uvicorn config.asgi:application`) and **Redis** for the channel layer.

## Quick start (group-scoped chat)

1. Create a group: `POST /api/group/create/` with `{ "name": "research", "tag": "workspace", "description": "..." }` (`tag` optional, default `workspace`)
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
| `/ws/chat/group/<name>/` | Group chat (`chat.ready` includes `group`, `tag`, `members`, `member_count`; `workspaces` when tag is `workspace`) |
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

- `chat.ready` — on connect (`conversation_id`, `active_branch_id`, **`agent_busy`** always `true` or `false`; when `true`, live stream auto-attaches)
- `chat.turn_started` — turn accepted (`turn_id`)
- `chat.reconnected` — reply to `chat.reconnect`
- `chat.status` — reply to `chat.status` (`agent_busy`, `turn_id`, `turn_started_at`)
- `chat.branch_updated` — active branch changed after compression
- Agent stream events — see `nodepoint/agent/schema.py` (via channel-layer fan-out)
- `chat.compress_started` — compression LLM call began (`message`)
- `chat.compress_completed` — summary ready (`message`, `summary_chars`)
- `chat.compressed` — internal context compression succeeded (new branch)
- `chat.compress_failed` — compression failed; turn continues on same branch (non-fatal)
- `chat.done` — turn finished (`turn_id`, optional `active_branch_id`)
- `chat.interrupted` — partial assistant text was saved (cancel or error mid-stream). Includes optional **`saved`** object: `{ "message_id", "content", "reasoning_content"? }`
- `chat.cancelled` — turn stopped by `chat.cancel` (channel fan-out). Includes optional **`saved`** with the same shape when partial text was persisted. If no turn was active, sent directly with `{ "message": "..." }`.

### Stop streaming (`chat.cancel`)

To stop generation and **save partial text received so far**, send `{ "type": "chat.cancel" }` (do not rely on closing the WebSocket alone).

Event order on successful cancel:

1. `chat.interrupted` with `saved` (partial assistant message persisted to Postgres)
2. `chat.cancelled` with the same `saved` block and `turn_id`

Merge `saved.content` into the UI immediately; optionally call REST once to reconcile `message_id` / ordering.

### Disconnect and reconnect (live stream)

If the WebSocket drops **during** a turn:

- The agent **keeps running** (not cancelled). Partial text is **not** saved until the turn completes, errors, or you send `chat.cancel`.
- Events are broadcast on the conversation channel group (`chat_{conversation_id}`), so **any** reconnect to the same workspace/group chat receives **live** tokens from that point on (no token replay).

After reconnect:

1. Open WebSocket again (or send `{ "type": "chat.reconnect" }` on an existing socket).
2. On `chat.ready` with `agent_busy: true`, you are already subscribed — live events flow immediately.
3. **Once**, call `GET /api/chat/<workspace>/` or `GET /api/chat/group/<name>/` to fill persisted messages that arrived while you were offline.
4. After `chat.cancelled` or `chat.interrupted`, use the `saved` payload first; REST refresh is the fallback.
5. Send `chat.send` only when `agent_busy` is false.

**Multi-worker:** Active turn metadata is stored in **Redis** (`nodepoint:chat:turn:{conversation_id}`), so `agent_busy` is consistent across uvicorn workers (`WEB_WORKERS>1`). `chat.cancel` reaches the owning worker via Redis pub/sub and waits until the turn is inactive before the cancel request completes on the requesting worker.

Use `ping` / `pong` for keepalive on long tool or LLM runs (`AGENT_REQUEST_TIMEOUT` defaults to 300s).

Multiple tabs on the same chat each receive the same live stream.

## Search scope

| Chat mode | `Knowledge.search_graph` searches |
|-----------|----------------------------------|
| Group (`/ws/chat/group/<name>/`) | Scoped by group `tag`: workspaces, files, entities, or relations in that group |
| Per-workspace | That workspace only |

If a group has no members, the tool returns a message that the group is empty.

## Concurrency

- Each WebSocket connection runs **one agent task** at a time (`Agent busy` if a second `chat.send` arrives during a turn). Turn active-state is in Redis, not process memory.
- Different users/workspaces run **in parallel** on the same `web` service (async event loop + httpx LLM streaming).
- Knowledge tools share a per-worker slot limit: `CHAT_MAX_CONCURRENT_SEARCHES` (default `8`).
- Scale horizontally: set `WEB_WORKERS` (default `4`) on the `web` container, or `docker compose up --scale web=2`.
- Document preprocess/embed jobs stay on the separate `worker` RQ service (not used for live chat).

## Environment

- `CHAT_COMPRESS_TOKEN_THRESHOLD` — default `80000`
- `CHAT_COMPRESS_MAX_TOOL_CHARS` — max chars per tool message in compression request (default `4000`)
- `CHAT_COMPRESS_MAX_ASSISTANT_CHARS` — max chars per assistant message (default `8000`)
- `CHAT_COMPRESS_MAX_MESSAGES` — max messages sent to compression (default `30`)
- `CHAT_COMPRESS_MAX_OUTPUT_TOKENS` — max tokens in the handoff report (default `4000`)
- `CHAT_COMPRESS_TEMPERATURE` — compression LLM temperature (default `0.2`)
- `CHAT_COMPRESS_OMIT_REASONING` — default `true` (omit `reasoning` field for vLLM-compatible gateways)
- `CHAT_COMPRESS_MODEL` — optional model override for summaries
- `LOG_LEVEL` — Django/app log level (default `INFO`)
- `CHAT_DEFAULT_SYSTEM` — system prompt for new chats
- `CHAT_MAX_CONCURRENT_SEARCHES` — default `8`
- `WEB_WORKERS` — uvicorn worker processes (default `4`; safe with Redis turn registry)
- `CHAT_TURN_REDIS_TTL` — seconds to retain active-turn metadata if a worker crashes (default `3600`)
- `CHAT_CANCEL_WAIT_TIMEOUT` — seconds to wait for remote turn cancel to finish (default `10`)
- `DB_CONN_MAX_AGE` — Postgres connection reuse per worker (default `60`)
- `AGENT_REQUEST_TIMEOUT` — LLM HTTP read timeout seconds (default `300`)
- `BASE_URL`, `API_KEY` — required for `Agent`
