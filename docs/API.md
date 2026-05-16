# Nodepoint API Reference

Complete reference for REST and WebSocket APIs. Base URL example: `http://localhost:8000`.

---

## Table of contents

1. [Overview](#overview)
2. [Conventions](#conventions)
3. [Workspace](#workspace)
4. [Documents](#documents)
5. [Preprocess](#preprocess)
6. [Knowledge graph](#knowledge-graph)
7. [Chat (REST)](#chat-rest)
8. [Chat (WebSocket)](#chat-websocket)
9. [Agent tools](#agent-tools)
10. [Environment](#environment)
11. [Endpoint index](#endpoint-index)

---

## Overview

| Surface | Prefix | Purpose |
|---------|--------|---------|
| REST | `/api/` | CRUD for workspaces, documents, graphs, chat |
| WebSocket | `/ws/chat/flagged/`, `/ws/chat/<workspace_name>/` | Live agent streaming (token-by-token) |
| Admin | `/admin/` | Django admin |
| Media (DEBUG) | `/media/` | Uploaded files |

```text
Flagged-scope (cross-workspace)          Per-workspace
  GET  /api/chat/?flagged=true             GET  /api/chat/<name>/
  WS   /ws/chat/flagged/                   WS   /ws/chat/<name>/
  GET  /api/knowledge-graph/?flagged=true  GET  /api/knowledge-graph/?workspace_name=<name>
```

### Starred workspaces (`is_flag=true`)

Star a workspace: `PATCH /api/workspace/<name>/toggle-flag/`.

| Use | API |
|-----|-----|
| All entities/relations (Postgres) for every starred workspace | `GET /api/knowledge-graph/?flagged=true` |
| Chat metadata for starred workspaces | `GET /api/chat/summary/?flagged=true` |
| Semantic search during **flagged-scope** chat | `Knowledge.search_graph` (starred workspaces only) |
| Semantic search during **per-workspace** chat | `Knowledge.search_graph` (that workspace + all starred) |

Documents and KG rows always live under **real** workspace names. There is no separate global KG database.

### Two chat modes (separate threads)

| Mode | REST | WebSocket | Message storage |
|------|------|-----------|-----------------|
| **Flagged-scope** | `GET/DELETE /api/chat/?flagged=true` | `/ws/chat/flagged/` | Internal `__flagged_chat__` (not listed in workspace list) |
| **Per-workspace** | `GET/DELETE /api/chat/<workspace_name>/` | `/ws/chat/<workspace_name>/` | That workspace’s conversation |

- No conversation UUID, no `POST` to create chat — lazy-create on first GET or WebSocket connect.
- Flagged-scope chat does **not** require any starred workspace to open; search needs at least one starred workspace for useful KG results.
- `GET /api/chat/123/` and flagged-scope chat are **different histories**, even if `123` is starred.

### Upload default

If `workspace_name` is omitted on upload, the file goes to the **first starred** workspace (`created_at` ascending). Returns `400` if no workspace is starred.

### Reserved names

Cannot create a workspace named `flagged`. Internal name `__flagged_chat__` is used only for flagged-scope message storage.

**Authentication** — None on these endpoints (add at the gateway if needed).

---

## Conventions

### Request format

| REST body | `Content-Type: application/json` |
| File upload | `multipart/form-data` |
| WebSocket | JSON text frames (one object per message) |

### Response format

| Success | JSON object or array (documented per endpoint) |
| Error | `{ "error": "<message>" }` with HTTP `400` / `404` |

### Types

| Type | Format |
|------|--------|
| UUID | String, e.g. `"660e8400-e29b-41d4-a716-446655440000"` |
| Timestamp | ISO 8601 UTC when `USE_TZ=True` |
| Document `status` | `PENDING`, `QUEUED`, `INPROGRESS`, `COMPLETED`, `FAILED`, `TERMINATED`, `INVALID` |

---

## Workspace

### `POST /api/workspace/create/`

Create a workspace and its media directory.

**Body**

```json
{ "name": "PRAJNA" }
```

**Response `200`**

```json
{
  "message": "Workspace created successfully",
  "workspace": {
    "name": "PRAJNA",
    "created_at": "2026-05-15T12:00:00.123456Z"
  }
}
```

| Status | Condition |
|--------|-----------|
| `400` | `name` missing or reserved (`flagged`) |

---

### `GET /api/workspace/list/`

**Response `200`** — array, newest first. Internal `__flagged_chat__` is omitted.

```json
[
  {
    "name": "PRAJNA",
    "is_flag": true,
    "created_at": "2026-05-15T12:00:00.123456Z"
  }
]
```

---

### `DELETE /api/workspace/delete/<name>/`

Deletes workspace row and `media/workspaces/<name>/`.

**Response `200`**

```json
{ "message": "Workspace deleted successfully" }
```

| Status | Condition |
|--------|-----------|
| `404` | Unknown workspace |

---

### `GET /api/workspace/<name>/flag-status/`

**Response `200`**

```json
{
  "workspace": "PRAJNA",
  "is_flag": true
}
```

---

### `PATCH /api/workspace/<name>/toggle-flag/`

Flips `is_flag` boolean. No body.

**Response `200`**

```json
{
  "message": "Workspace flag updated successfully",
  "workspace": "PRAJNA",
  "is_flag": false
}
```

---

## Documents

Allowed extensions: **`.txt`**, **`.md`**, **`.text`**.

Upload triggers background preprocessing (KG extraction → Postgres → Qdrant vectors).

### `POST /api/document/upload/`

**Content-Type:** `multipart/form-data`

| Field | Required | Default |
|-------|----------|---------|
| `workspace_name` | no | first starred workspace (`is_flag=true`, oldest `created_at`) |
| `file` | yes | — |

**Response `200`**

```json
{
  "message": "File uploaded successfully",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "file_name": "notes.md",
  "file_path": "/path/to/media/workspaces/PRAJNA/notes.md",
  "file_url": "/media/workspaces/PRAJNA/notes.md",
  "status": "PENDING"
}
```

| Status | Condition |
|--------|-----------|
| `400` | Missing file, bad extension, or no starred workspace when `workspace_name` omitted |
| `404` | Workspace not found |

---

### `GET /api/document/<workspace_name>/`

**Response `200`**

```json
{
  "workspace": "PRAJNA",
  "total_files": 1,
  "files": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "file_name": "notes.md",
      "file_url": "/media/workspaces/PRAJNA/notes.md",
      "status": "COMPLETED",
      "content": true,
      "uploaded_at": "2026-05-15T12:00:00.123456Z"
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `content` | `true` if extracted text is stored |

---

### `DELETE /api/document/delete/<workspace_name>/<file_name>/`

**Response `200`**

```json
{ "message": "Document deleted successfully" }
```

---

## Preprocess

### `POST /api/workspace/preprocess/<workspace_name>/`

Re-queues KG + vector pipeline for every document in the workspace.

**Response `200`**

```json
{
  "message": "Preprocessing queued for workspace 'PRAJNA'"
}
```

---

## Knowledge graph

Source of truth: **Postgres** (`KnowledgeEntity`, `KnowledgeRelation`).  
Semantic search: **Qdrant** (via agent tool `Knowledge.search_graph` during chat).

Entities and relations are always stored per **document** under a **named workspace**. Flagged-scope chat does not create its own nodes or edges; use `?flagged=true` to read the union of all starred workspaces’ graphs.

### `GET /api/knowledge-graph/`

Provide **exactly one** query mode.

| Query | Result |
|-------|--------|
| `?workspace_name=PRAJNA` | One object: all nodes/edges for that workspace |
| `?flagged=true` | All nodes/edges for **every** starred workspace, grouped per workspace |

### Single workspace — `?workspace_name=<name>`

**Single workspace `200`**

```json
{
  "workspace": "PRAJNA",
  "nodes": [
    {
      "id": "…",
      "name": "Alice",
      "entity_type": "PER",
      "attributes": { "role": "engineer" },
      "document_id": "…",
      "file_name": "notes.md",
      "vector": "COMPLETED",
      "created_at": "2026-05-15T12:00:00Z"
    }
  ],
  "edges": [
    { "source": "Alice", "target": "Acme Corp" }
  ]
}
```

| Field | Notes |
|-------|-------|
| `nodes[]` | Full entity rows for documents in this workspace |
| `edges[].source` / `target` | Entity **names** only (not UUIDs) |
| `nodes[].vector` | Vector index status for that entity |

### All starred workspaces — `?flagged=true`

Returns **every** `KnowledgeEntity` and `KnowledgeRelation` for each workspace with `is_flag=true`, in one response. This is the REST way to load “global” KG data across starred corpora (same scope as flagged-scope chat search, but full Postgres dump, not semantic search).

**Example**

```bash
curl "http://localhost:8000/api/knowledge-graph/?flagged=true"
```

**Response `200`**

```json
{
  "graphs": [
    {
      "workspace": "main",
      "nodes": [
        {
          "id": "…",
          "name": "Alice",
          "entity_type": "PER",
          "attributes": { "role": "engineer" },
          "document_id": "…",
          "file_name": "notes.md",
          "vector": "COMPLETED",
          "created_at": "2026-05-15T12:00:00Z"
        }
      ],
      "edges": [
        { "source": "Alice", "target": "Acme Corp" }
      ]
    },
    {
      "workspace": "research",
      "nodes": [],
      "edges": []
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `graphs` | One entry per starred workspace (sorted by workspace name) |
| `graphs[].nodes` | All entities from documents in that workspace |
| `graphs[].edges` | All relations from documents in that workspace |

**Client notes**

- There is **no** single merged `nodes` / `edges` array; concatenate per workspace or render one panel per `graphs[i]`.
- Empty `graphs: []` means no workspace is starred.
- Empty `nodes` / `edges` for a workspace means no KG was ingested there yet (upload + preprocess).
- Entity names may repeat across workspaces; use `id` + `workspace` (from parent object) as unique keys in UI.

**Relation to chat**

| API | Data |
|-----|------|
| `GET /api/knowledge-graph/?flagged=true` | Full entity/relation lists (Postgres) |
| Flagged chat `Knowledge.search_graph` | Top semantic hits (Qdrant → Postgres resolve → markdown) |

| Status | Condition |
|--------|-----------|
| `400` | Both or neither query param |
| `404` | Unknown `workspace_name` (single-workspace mode only) |

---

## Chat (REST)

One implicit chat per **workspace name**. No conversation UUID in the API.  
REST returns **persisted** user-visible messages (root branch only). **Live streaming** is WebSocket only. Compression branches are server-internal (no branch REST API).

**Agent behavior (WebSocket):**

- Fixed system prompt: answer only from `Knowledge.search_graph` tool output and prior search tool messages in the thread.
- **Single tool:** `Knowledge.search_graph` only (no MCP tools in chat).
- Citations in replies: **`[source: file_name]`** (exact `file_name` from tool output).
- No hallucination: if search has no relevant records, the agent must say so.

| Concern | REST | WebSocket |
|---------|------|-----------|
| History | Root messages via GET | N/A (use GET after turn) |
| Live reply | No | Token-by-token stream |
| Tools | Not in GET response | `tool_calls` / `tool_completed` events |

### `GET /api/chat/?flagged=true`

Flagged-scope chat only. Query parameter **required**.

| Query | Required |
|-------|----------|
| `flagged=true` | yes |

`GET /api/chat/` without `flagged=true` → `400`.

Lazy-creates on first access. Always `200` even when `starred_workspaces` is empty.

**Response `200`**

```json
{
  "flagged": true,
  "starred_workspaces": ["main", "research"],
  "messages": [
    {
      "id": "…",
      "role": "system",
      "content": "You are a workspace knowledge assistant…",
      "sequence": 0,
      "created_at": "2026-05-15T12:00:00Z"
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `flagged` | Always `true` for this endpoint |
| `starred_workspaces` | Names of workspaces with `is_flag=true` (KG search scope) |
| `messages` | Root-branch history for flagged-scope chat only |

### `DELETE /api/chat/?flagged=true`

Full reset of **flagged-scope** chat only (does not clear per-workspace chats).

**Response `200`**

```json
{
  "message": "Flagged-scope chat cleared",
  "flagged": true,
  "starred_workspaces": ["main", "research"]
}
```

---

### `GET /api/chat/<workspace_name>/`

Lazy-creates that workspace's chat on first access. Unknown or reserved name → `404`.

**Response `200`**

```json
{
  "workspace": "main",
  "is_flag": true,
  "messages": []
}
```

**Message object**

```json
{
  "id": "990e8400-e29b-41d4-a716-446655440000",
  "role": "user",
  "content": "Hello",
  "reasoning_content": null,
  "tool_calls": null,
  "tool_call_id": null,
  "tool_name": null,
  "sequence": 1,
  "created_at": "2026-05-15T12:01:00Z"
}
```

| `role` | Notes |
|--------|-------|
| `system` | System prompt |
| `user` | User message |
| `assistant` | May include `tool_calls`, `reasoning_content` |
| `tool` | Tool result; `tool_call_id`, `tool_name` set |

---

### `DELETE /api/chat/<workspace_name>/`

Full reset: clears messages, compression history, and internal branches; recreates a fresh chat with the system prompt only.

**Response `200`**

```json
{
  "message": "Chat cleared",
  "workspace": "main"
}
```

| Status | Condition |
|--------|-----------|
| `404` | Unknown or reserved workspace name |

---

### `GET /api/chat/summary/`

Same query rules as knowledge graph: `workspace_name` **or** `flagged=true`.

**Single workspace `200`**

```json
{
  "workspace": "PRAJNA",
  "is_flag": true,
  "updated_at": "2026-05-15T12:30:00Z",
  "message_count": 12
}
```

**Flagged `200`**

```json
{
  "workspaces": [
    {
      "workspace": "PRAJNA",
      "is_flag": true,
      "updated_at": null,
      "message_count": 0
    }
  ]
}
```

---

## Chat (WebSocket)

**Requires:** ASGI server (`uvicorn config.asgi:application`) and Redis (Channels layer).

| URL | Chat mode |
|-----|-----------|
| `ws://<host>/ws/chat/flagged/` | Flagged-scope (cross-workspace search) |
| `ws://<host>/ws/chat/<workspace_name>/` | Single workspace |

Reserved path segment: `flagged` is not a user workspace name.

### Typical client flow (flagged-scope)

1. Star workspaces: `PATCH /api/workspace/main/toggle-flag/`
2. `GET /api/knowledge-graph/?flagged=true` — optional, load all entities/relations for UI
3. `GET /api/chat/?flagged=true` — load flagged chat history
4. Connect `ws://<host>/ws/chat/flagged/` → `chat.ready`
5. Send `chat.send` → stream → `chat.done`
6. `GET /api/chat/?flagged=true` again — refresh messages

### Typical client flow (per-workspace)

1. `GET /api/chat/PRAJNA/` → load history
2. Connect `ws://<host>/ws/chat/PRAJNA/` → `chat.ready`
3. Same send/stream/refresh pattern

---

### Client → server

One JSON object per text frame.

#### Ping

```json
{ "type": "ping" }
```

**Response:** `{ "type": "pong" }`

#### Send message

```json
{
  "type": "chat.send",
  "content": "What entities are in the graph?",
  "exclude_servers": ["SomeMcpServer"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `content` | yes | User message (non-empty after trim) |
| `exclude_servers` | no | Tool servers to omit from this turn |

| Server reply | Condition |
|--------------|-----------|
| `{ "type": "error", "message": "Agent busy" }` | Previous turn still running |
| Stream of events + `chat.done` | Success |

#### Cancel

```json
{ "type": "chat.cancel" }
```

**Response:** `{ "type": "chat.cancelled" }`

---

### Server → client: message categories

Every WebSocket frame is a JSON object. The **`type` field is the event category**.

There are three layers:

| Layer | Purpose | How to identify |
|-------|---------|-----------------|
| **Section wrappers** | UI grouping (open/close blocks) | `type: "section"` |
| **Token stream** | One character/word chunk at a time | `type: "thinking_token"` or `"assistant_response_token"` |
| **Lifecycle / tools** | Whole events (not per-character) | Other `type` values below |

---

### Token-by-token streaming (detailed)

For **assistant text**, the server sends **one WebSocket message per token** (small string chunk). The category is the event `type` — there is no separate `category` field.

#### 1. Thinking tokens (reasoning)

Model reasoning / chain-of-thought. Usually hidden or shown in a collapsible “thinking” panel.

```json
{
  "type": "thinking_token",
  "token": "Let"
}
```

```json
{
  "type": "thinking_token",
  "token": " me check"
}
```

#### 2. Response tokens (user-visible answer)

Normal assistant reply text.

```json
{
  "type": "assistant_response_token",
  "token": "Hello"
}
```

```json
{
  "type": "assistant_response_token",
  "token": "!"
}
```

**Client pattern:** append `token` to the buffer for the current section until the section closes or the type changes.

#### 3. Section wrappers (optional UI hints)

Before/after token blocks, the server may send **section** frames so the client knows which panel to update:

```json
{
  "type": "section",
  "section": "thinking",
  "action": "open"
}
```

```json
{
  "type": "section",
  "section": "thinking",
  "action": "close"
}
```

```json
{
  "type": "section",
  "section": "response",
  "action": "open"
}
```

```json
{
  "type": "section",
  "section": "response",
  "action": "close"
}
```

| `section` | `action` | Meaning |
|-----------|----------|---------|
| `thinking` | `open` / `close` | Reasoning block |
| `response` | `open` / `close` | User-visible answer block |
| `tool_calls` | `open` / `close` | Tool invocation phase |
| `tool_completed` | `open` / `close` | Single tool finished |

Sections are **hints** for layout. Token categorization still comes from `thinking_token` vs `assistant_response_token`.

#### Example transcript (one turn)

```text
→ client:  { "type": "chat.send", "content": "Hi" }

← server:  { "type": "section", "section": "thinking", "action": "open" }
← server:  { "type": "thinking_token", "token": "The" }
← server:  { "type": "thinking_token", "token": " user" }
← server:  { "type": "section", "section": "thinking", "action": "close" }
← server:  { "type": "section", "section": "response", "action": "open" }
← server:  { "type": "assistant_response_token", "token": "Hi" }
← server:  { "type": "assistant_response_token", "token": " there!" }
← server:  { "type": "section", "section": "response", "action": "close" }
← server:  { "type": "chat.done" }
```

#### What is NOT token-by-token

Tools and control events are **single frames** (full payload per event):

| `type` | When | Key fields |
|--------|------|------------|
| `tool_calls` | Agent chose tools | `names`: string[] |
| `tool_call_start` | Tool arg stream begins | `tool_call_id`, `tool_name` |
| `tool_call_delta` | Tool args chunk | `tool_call_id`, `arguments_delta` |
| `tool_call_end` | Tool arg stream ends | `tool_call_id` |
| `assistant_tool_calls_message` | Full tool-call message | `tool_calls`, `content`, `reasoning_content` |
| `tool_completed` | Tool finished | `tool_name`, `tool_call_id`, `ok` |
| `tool_result` | Raw tool result (also forwarded) | `result`, `ok` |
| `agent_turn_start` | Model round started | `turn_index` |
| `model_turn_complete` | Model round ended | `finish_reason` |
| `agent_session_done` | Agent loop finished text turn | — |
| `chat.compressed` | Context compression (server switched branch internally) | — |
| `chat.done` | Entire user turn complete | — |
| `error` | Failure | `message` |

**Example with tools**

```text
← { "type": "section", "section": "tool_calls", "action": "open" }
← { "type": "tool_calls", "names": ["Knowledge.search_graph"] }
← { "type": "section", "section": "tool_calls", "action": "close" }
← { "type": "section", "section": "tool_completed", "action": "open" }
← { "type": "tool_completed", "tool_name": "Knowledge.search_graph", "tool_call_id": "call_1", "ok": true }
← { "type": "section", "section": "tool_completed", "action": "close" }
← … more thinking/response tokens …
← { "type": "chat.done" }
```

---

### Connection lifecycle events

#### `chat.ready` (on connect)

**Per-workspace** (`/ws/chat/PRAJNA/`):

```json
{
  "type": "chat.ready",
  "workspace": "PRAJNA"
}
```

**Flagged-scope** (`/ws/chat/flagged/`):

```json
{
  "type": "chat.ready",
  "flagged": true,
  "starred_workspaces": ["main", "research"]
}
```

| Field | Meaning |
|-------|---------|
| `workspace` | Per-workspace mode only |
| `flagged` | Flagged-scope mode only |
| `starred_workspaces` | Workspaces included in `Knowledge.search_graph` for this connection |

Close codes: `4000` invalid URL; `4004` unknown workspace (per-workspace mode).

#### `chat.done` (after each `chat.send` turn)

```json
{ "type": "chat.done" }
```

Persisted messages:

- Flagged-scope → `GET /api/chat/?flagged=true`
- Per-workspace → `GET /api/chat/<workspace_name>/`

---

### Context compression

When the active thread exceeds **`CHAT_COMPRESS_TOKEN_THRESHOLD`** (default **64000**):

1. Server generates a compression report (not sent to client).
2. Creates an **internal** child branch with a handoff user message (model-only).
3. Switches `active_branch_id` to the new branch.
4. Emits `{ "type": "chat.compressed" }` (no branch IDs exposed).

REST history (`messages` on GET chat) stays on the **root** branch only.

---

### Client implementation checklist

- [ ] Parse each frame as JSON; branch on `type`.
- [ ] For `thinking_token` / `assistant_response_token`, append `token` to the correct UI buffer.
- [ ] Use `section` open/close to swap panels (optional; types alone are enough).
- [ ] Ignore unknown `type` values or log them for forward compatibility.
- [ ] On `chat.done`, refresh history from REST if needed.
- [ ] On `chat.compressed`, optional UI hint that context was summarized (server handles branch switch).
- [ ] Do not send another `chat.send` until `chat.done` or `error` (or cancel).

Event definitions: `nodepoint/agent/schema.py` (`StreamEventType`, `ThinkingTokenEvent`, `AssistantResponseTokenEvent`, …).

---

## Agent tools

Invoked by the agent during WebSocket turns (function calling). Not HTTP endpoints.

Registered when `nodepoint/registry/tools/Knowledge.py` has `active: true` in its module docstring.

### `Knowledge.search_graph`

Semantic search in **Qdrant**, then **resolves each hit ID** to full Postgres entity/relation rows. Returns a **markdown search document** (string), not raw JSON hits.

Workspace scope is **automatic** (model must not pass `workspace`):

| Chat connection | Workspaces searched in Qdrant |
|-----------------|-------------------------------|
| `/ws/chat/flagged/` | All starred (`is_flag=true`) only |
| `/ws/chat/<name>/` | That workspace **plus** all starred |

If flagged-scope chat runs and **no** workspace is starred, the tool returns plain text:

```text
No workspace is flagged (starred). Star at least one workspace (is_flag=true) to include it in knowledge search.
```

Prior search hit IDs from the same chat session are merged into later searches in that session.

**Arguments**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `query` | string | required | Natural-language query |
| `limit` | int | `10` | Max Qdrant hits |
| `record_type` | string \| null | `null` | Filter: `entity` or `relation` |

**Return:** markdown string with `## [source: file_name]` sections for citations.

```markdown
# Knowledge search: "who is Alice"

## [source: notes.md]
**Entity** (score 0.8700)
- name: Alice
- type: PER
- attributes: {'role': 'eng'}
- workspace: PRAJNA
```

---

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `CHAT_COMPRESS_TOKEN_THRESHOLD` | `64000` | Trigger internal branch compression |
| `CHAT_DEFAULT_SYSTEM` | (see settings) | New conversation system prompt |
| `BASE_URL`, `API_KEY` | — | LLM provider for `Agent` |
| `POSTGRES_*`, `MONGO_*`, Redis, Qdrant | — | Data stores (`settings.toml`, `.env`) |

---

## Endpoint index

### REST

| Method | Path |
|--------|------|
| POST | `/api/workspace/create/` |
| GET | `/api/workspace/list/` |
| DELETE | `/api/workspace/delete/<name>/` |
| GET | `/api/workspace/<name>/flag-status/` |
| PATCH | `/api/workspace/<name>/toggle-flag/` |
| POST | `/api/document/upload/` |
| GET | `/api/document/<workspace_name>/` |
| DELETE | `/api/document/delete/<workspace_name>/<file_name>/` |
| POST | `/api/workspace/preprocess/<workspace_name>/` |
| GET | `/api/knowledge-graph/` |
| GET | `/api/chat/?flagged=true` |
| DELETE | `/api/chat/?flagged=true` |
| GET | `/api/chat/<workspace_name>/` |
| DELETE | `/api/chat/<workspace_name>/` |
| GET | `/api/chat/summary/` |

### WebSocket

| Direction | Path / event |
|-----------|----------------|
| Connect | `ws://<host>/ws/chat/flagged/` or `ws://<host>/ws/chat/<workspace_name>/` |
| Client | `ping`, `chat.send`, `chat.cancel` |
| Server | `chat.ready`, token stream, sections, tools, `chat.compressed`, `chat.done`, `error`, `pong` |
