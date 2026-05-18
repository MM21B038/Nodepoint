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
  GET  /api/knowledge-graph/entity-types/?flagged=true
  GET  /api/knowledge-graph/?flagged=true  GET  /api/knowledge-graph/?workspace_name=<name>
```

### Starred workspaces (`is_flag=true`)

Star a workspace: `PATCH /api/workspace/<name>/toggle-flag/`.

| Use | API |
|-----|-----|
| Entity type counts per starred workspace | `GET /api/knowledge-graph/entity-types/?flagged=true` |
| Filtered subgraph per starred workspace | `GET /api/knowledge-graph/?flagged=true` (+ optional `entity_type`, `depth`, `limit`) |
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

Upload triggers a **4-step global preprocess pipeline** (see [Preprocess](#preprocess)).

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
  "pipeline": {
    "message": "Preprocess pipeline queued: prepare document → chunk KG (parallel) → embeddings → mongo repair",
    "steps": [
      "prepare_document",
      "chunk_preprocess",
      "vector_preprocess",
      "chunk_mongo_repair"
    ],
    "jobs": {
      "prepare_document": "rq-job-id-1",
      "chunk_preprocess": "rq-job-id-2",
      "vector_preprocess": "rq-job-id-3",
      "chunk_mongo_repair": "rq-job-id-4"
    }
  },
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

Queues the **full workspace preprocess pipeline** for the named workspace (no single-document upload). One call runs **prepare legacy → chunk KG (parallel workers) → embeddings → mongo repair** — no separate manual preprocess needed to migrate old documents.

**Pipeline steps (RQ orchestrator)**

| Step | Job | What it does |
|------|-----|----------------|
| 1 (upload only) | `run_prepare_document` | Split the new file → Postgres `DocumentChunk` rows + Mongo `chunk_content` |
| 1 (POST only) | `run_prepare_legacy_batch` | For documents in this workspace with zero chunks or `content=false`, run `prepare_document` (chunk migration) |
| 2 | `run_chunk_preprocess_batch` | Enqueue `process_chunk` (5 min timeout) for incomplete chunks in the workspace |
| 3 | `run_vector_preprocess_batch` | Enqueue embeddings for entities, relations, and **chunks** (PENDING/FAILED vectors) in the workspace |
| 4 | `run_chunk_mongo_repair_batch` | Rebuild Mongo chunk text for `content=false` or missing chunk bodies in the workspace |

**Order (sequential steps, parallel workers inside step 2):** prepare → chunk batch → **vectors after chunk batch** → mongo repair. Vectors no longer run in parallel with chunk KG.

**Legacy documents:** Files uploaded before chunk migration may show `document_status: COMPLETED` with **no** `DocumentChunk` rows and `chunk_id=null` on KG rows. POST preprocess backfills chunks; poll preprocess-status until `overall.ready` is true.

**Per chunk:** Mongo stores chunk text; KG extraction runs in parallel RQ workers (`process_chunk`). A document is marked `COMPLETED` only when **all** its chunks reach `COMPLETED`.

**Response `200`**

```json
{
  "message": "Preprocess pipeline queued: prepare legacy → chunk KG (parallel) → embeddings → mongo repair (workspace=PRAJNA)",
  "pipeline": {
    "message": "Preprocess pipeline queued: prepare legacy → chunk KG (parallel) → embeddings → mongo repair (workspace=PRAJNA)",
    "steps": [
      "prepare_legacy",
      "chunk_preprocess",
      "vector_preprocess",
      "chunk_mongo_repair"
    ],
    "jobs": {
      "prepare_legacy": "rq-job-id-1",
      "chunk_preprocess": "rq-job-id-2",
      "vector_preprocess": "rq-job-id-3",
      "chunk_mongo_repair": "rq-job-id-4"
    }
  }
}
```

---

### `GET /api/workspace/<workspace_name>/preprocess-status/`

Read-only pipeline status for a workspace: upload queue → document processing → KG in Postgres → Qdrant embeddings.

Poll after upload until `overall.ready` is `true` and `overall.phase` is `ready`.

**Response `200`**

```json
{
  "workspace": "PRAJNA",
  "overall": {
    "phase": "embedding",
    "ready": false,
    "documents_total": 2,
    "documents_failed": 0
  },
  "documents": {
    "by_status": {
      "PENDING": 0,
      "QUEUED": 0,
      "INPROGRESS": 0,
      "COMPLETED": 2,
      "FAILED": 0,
      "INVALID": 0,
      "TERMINATED": 0
    }
  },
  "vectors": {
    "entities": { "total": 10, "pending": 2, "completed": 8, "failed": 0 },
    "relations": { "total": 5, "pending": 1, "completed": 4, "failed": 0 },
    "chunks": { "total": 3, "pending": 0, "completed": 3, "failed": 0 }
  },
  "files": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "file_name": "notes.md",
      "document_status": "COMPLETED",
      "content": true,
      "legacy": false,
      "phase": "embedding",
      "uploaded_at": "2026-05-15T12:00:00.123456Z",
      "chunks": {
        "total": 3,
        "pending": 0,
        "queued": 0,
        "in_progress": 0,
        "completed": 3,
        "failed": 0
      },
      "entities": { "total": 5, "pending": 1, "completed": 4, "failed": 0 },
      "relations": { "total": 2, "pending": 0, "completed": 2, "failed": 0 },
      "chunk_vectors": { "total": 3, "pending": 1, "completed": 2, "failed": 0 },
      "embedding_progress": 0.8571
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `overall.phase` | Workspace-wide stage: `idle`, `needs_prepare`, `queued`, `processing`, `kg_ready`, `embedding`, `ready`, `failed` |
| `overall.ready` | All files are `ready` and none failed |
| `document_status` | Raw `Document.status` from Postgres (COMPLETED when all chunks are COMPLETED) |
| `phase` (per file) | Derived from chunk KG status + vector progress (see table below) |
| `legacy` | `true` when the document has `content=true` but no `DocumentChunk` rows yet (pre-migration KG only) |
| `chunks` | Per-file chunk processing counts by `DocumentChunk.status` |
| `chunk_vectors` | Per-file Qdrant embedding progress for chunk points |
| `embedding_progress` | Share of entity + relation + chunk vectors with `COMPLETED` (0–1) |
| `vectors` | Workspace totals for entity, relation, and chunk vector jobs |

**Per-file `phase` values**

| `phase` | When |
|---------|------|
| `idle` | No documents (workspace-level only) |
| `needs_prepare` | No chunks yet (`content=false`) or completed doc with no chunks/KG (run POST preprocess to migrate) |
| `queued` | Document `PENDING` or `QUEUED` (awaiting chunk workers) |
| `processing` | Chunks still running KG (`process_chunk`) or document `INPROGRESS` during prepare |
| `kg_ready` | All chunks `COMPLETED` but no entity rows yet |
| `embedding` | Chunk KG done (or legacy doc with entity rows only) and some vectors not `COMPLETED` |
| `ready` | All vectors `COMPLETED` (legacy: entity/relation vectors only when there are no chunks) |
| `failed` | Document `FAILED`, `INVALID`, or `TERMINATED`, or chunk failures |

**Important:** `document_status: COMPLETED` with zero chunks usually means a **legacy** document (KG before chunk migration). Use `phase` `needs_prepare` / `legacy: true` and POST preprocess to backfill chunks. After migration, `COMPLETED` means all chunks finished KG ingest. Embeddings run in pipeline step 3 (`vector_preprocess`); use `phase` or `embedding_progress` for Qdrant readiness.

| Status | Condition |
|--------|-----------|
| `404` | Workspace not found |

---

## Knowledge graph

Source of truth: **Postgres** (`KnowledgeEntity`, `KnowledgeRelation`).  
Semantic search: **Qdrant** (via agent tool `Knowledge.search_graph` during chat).

Entities and relations are always stored per **document** under a **named workspace**. Flagged-scope chat does not create its own nodes or edges; use `?flagged=true` to read the union of all starred workspaces’ graphs.

### `GET /api/knowledge-graph/entity-types/`

Provide **exactly one** scope: `workspace_name` or `flagged=true`.

Returns distinct `entity_type` values with entity counts, sorted by count descending.

**Single workspace `200`**

```json
{
  "workspace": "PRAJNA",
  "entity_types": [
    { "type": "PER", "count": 42 },
    { "type": "ORG", "count": 10 }
  ]
}
```

**Flagged `200`**

```json
{
  "workspaces": [
    { "workspace": "PRAJNA", "entity_types": [{ "type": "PER", "count": 42 }] }
  ]
}
```

| Status | Condition |
|--------|-----------|
| `400` | Both or neither scope param; invalid filters on graph endpoint only |
| `404` | Unknown `workspace_name` |

---

### `GET /api/knowledge-graph/`

Provide **exactly one** scope: `workspace_name` or `flagged=true`.

Optional filters (same for both modes; applied **per workspace** in flagged responses):

| Query | Default | Max | Description |
|-------|---------|-----|-------------|
| `entity_type` | — | — | Comma-separated, e.g. `PER,ORG` (case-sensitive) |
| `depth` | `1` | `5` | BFS hops from seed entities |
| `limit` | `500` | `5000` | Max nodes per workspace |

**Traversal**

1. **Seeds:** entities matching `entity_type` when set; otherwise all entities in the workspace.
2. **BFS:** expand via relations up to `depth` hops; stop at `limit` nodes.
3. **Edges:** relations whose `source_id` and `target_id` are both in the node set (edges between seeds appear even when `depth=0`).

**Single workspace `200`**

```bash
curl "http://localhost:8000/api/knowledge-graph/?workspace_name=PRAJNA&entity_type=PER,ORG&depth=1&limit=500"
```

```json
{
  "workspace": "PRAJNA",
  "filters": { "entity_types": ["PER", "ORG"], "depth": 1, "limit": 500 },
  "truncated": false,
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
    {
      "id": "…",
      "source": "Alice",
      "target": "Acme Corp",
      "source_id": "…",
      "target_id": "…",
      "type_description": "works at"
    }
  ]
}
```

**Flagged `200`**

```bash
curl "http://localhost:8000/api/knowledge-graph/?flagged=true&entity_type=PER&limit=100"
```

```json
{
  "graphs": [
    {
      "workspace": "main",
      "filters": { "entity_types": ["PER"], "depth": 1, "limit": 100 },
      "truncated": false,
      "nodes": [...],
      "edges": [...]
    }
  ]
}
```

| Field | Notes |
|-------|-------|
| `filters.entity_types` | `null` when no `entity_type` query param |
| `truncated` | `true` if seeds or BFS hit `limit` |
| `edges[]` | Includes `id`, `source_id`, `target_id`, `type_description` |

**Client notes**

- No merged global `nodes` array; use one panel per `graphs[i]`.
- Empty `graphs: []` — no starred workspaces (internal `__flagged_chat__` excluded).
- Request higher `limit` / `depth` explicitly for larger subgraphs (defaults cap at 500 nodes).

**Relation to chat**

| API | Data |
|-----|------|
| `GET /api/knowledge-graph/?flagged=true` | Filtered Postgres subgraph per starred workspace |
| Flagged chat `Knowledge.search_graph` | Top semantic hits (Qdrant → Postgres → markdown) |

| Status | Condition |
|--------|-----------|
| `400` | Both or neither scope; invalid `depth`/`limit`; empty `entity_type` after parse |
| `404` | Unknown `workspace_name` |

---

## Chat (REST)

One implicit chat per **workspace name**. No conversation UUID in the API.  
REST returns **persisted** user-visible messages (root branch only). **Live streaming** is WebSocket only. Compression branches are server-internal (no branch REST API).

**Agent behavior (WebSocket):**

- Fixed system prompt: answer only from `Knowledge.search_graph` tool output and prior search tool messages in the thread.
- **Knowledge tools:** `search_graph`, `get_entity_record`, `get_relation_record`, `get_chunk_record`, `get_document_record`, `search_entity_by_name` (no MCP tools in chat).
- Citations in replies: **`[entity](uuid)`**, **`[relation](uuid)`**, **`[chunk](uuid)`**, or **`[doc](uuid)`** only — never `[source: file_name]`.
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
2. `GET /api/knowledge-graph/entity-types/?flagged=true` and `GET /api/knowledge-graph/?flagged=true&entity_type=...` — optional KG UI data
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

**Hybrid search:** Qdrant semantic retrieval (`candidate_limit`, default **4000**), then **BM25 + fuzzy** rerank on resolved text, then weighted fusion. Returns structured **markdown** with record ids, `chunk_id`, content, and score breakdown.

Also available: `Knowledge.get_entity_record`, `Knowledge.get_relation_record`, `Knowledge.get_chunk_record`, `Knowledge.get_document_record`, `Knowledge.search_entity_by_name`.

Tool output includes a **cite** line per result, e.g. `[entity](uuid)` and parent `[doc](document-uuid)`. The model must copy these — not `[source: file_name]`.

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
| `limit` | int | `10` | Final results after rerank |
| `record_type` | string \| null | `null` | Filter: `entity`, `relation`, or `chunk` |
| `candidate_limit` | int | `4000` | Qdrant pool size before BM25/fuzzy |
| `semantic_weight` | float | `0.6` | Weight for cosine score (normalized) |
| `lexical_weight` | float | `0.4` | Weight for BM25+fuzzy blend |
| `bm25_weight` | float | `0.5` | Share of lexical from BM25 vs fuzzy |

**Return:** markdown with `## Result N — kind [kind](uuid)`, ids, chunk_id, content, and scores.

### `Knowledge.get_entity_record` / `get_relation_record` / `get_chunk_record` / `get_document_record`

| Name | Type | Description |
|------|------|-------------|
| `entity_id` / `relation_id` / `chunk_id` / `document_id` | string | Postgres UUID |

Returns full record markdown including content. Documents return assembled text (Mongo chunks or file). Cite as `[doc](document_id)`.

### `Knowledge.search_entity_by_name`

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `name` | string | required | Entity name to search |
| `exact` | bool | `false` | If true, case-insensitive exact name; if false, fuzzy match |
| `limit` | int | `20` | Max entities |
| `threshold` | float | `0.6` | Min fuzzy score 0–1 (ignored when `exact=true`) |

Returns matches with `score` (fuzzy mode), outgoing/incoming relations (relation ids and peer entity ids).

---

### `GET /api/knowledge/entities/search/`

Fuzzy entity **name** search (rapidfuzz `token_set_ratio`) plus a subgraph around matches using `depth` and `limit`.

**Scope:** exactly one of `workspace_name` or `flagged=true` (same rules as knowledge-graph).

| Query | Required | Default | Max | Description |
|-------|----------|---------|-----|-------------|
| `q` | yes | — | — | Name query (typos tolerated via fuzzy) |
| `threshold` | no | `0.6` | `1.0` | Min match score `0`–`1` |
| `match_limit` | no | `20` | `100` | Max ranked entity matches (seeds) |
| `depth` | no | `1` | `5` | BFS hops from seeds into the graph |
| `limit` | no | `500` | `5000` | Max nodes in `graph` |
| `entity_type` | no | — | — | Comma-separated filter on candidates, e.g. `PER,ORG` |

**Single workspace `200`**

```bash
curl "http://localhost:8000/api/knowledge/entities/search/?q=Alcie&workspace_name=PRAJNA&threshold=0.6&depth=1&limit=100"
```

```json
{
  "query": "Alcie",
  "workspace": "PRAJNA",
  "filters": {
    "entity_types": null,
    "depth": 1,
    "limit": 100,
    "threshold": 0.6,
    "match_limit": 20
  },
  "matches": [
    {
      "id": "…",
      "name": "Alice",
      "entity_type": "PER",
      "score": 0.92,
      "workspace": "PRAJNA",
      "document_id": "…",
      "file_name": "notes.md",
      "vector": "COMPLETED",
      "created_at": "…"
    }
  ],
  "graph": {
    "workspace": "PRAJNA",
    "truncated": false,
    "nodes": […],
    "edges": […]
  }
}
```

- **`matches`:** entities with `score >= threshold` (empty if none).
- **`graph`:** BFS from match entity ids; edges are the induced subgraph on returned nodes.

**Flagged `200`**

```json
{
  "query": "Alice",
  "filters": { "depth": 1, "limit": 500, "threshold": 0.6, "match_limit": 20, "entity_types": null },
  "workspaces": [
    {
      "workspace": "main",
      "matches": […],
      "graph": { "workspace": "main", "truncated": false, "nodes": […], "edges": […] }
    }
  ]
}
```

| Status | Condition |
|--------|-----------|
| `400` | Missing `q`; invalid scope; invalid `threshold` / `depth` / `limit` / `match_limit` |
| `404` | Unknown `workspace_name` |

Chat tool `Knowledge.search_entity_by_name` uses the same fuzzy matcher when `exact=false`.

---

## Knowledge records (REST)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/knowledge/entity/<uuid>/` | Entity JSON (`id`, `chunk_id`, `content`, …) |
| GET | `/api/knowledge/relation/<uuid>/` | Relation JSON |
| GET | `/api/knowledge/chunk/<uuid>/` | Chunk JSON (full Mongo text in `content`) |
| GET | `/api/knowledge/document/<uuid>/` | Document JSON (`kind`: `doc`, full `content` text) |

Optional query: `?workspace_name=` — returns `404` if the record is not in that workspace.

---

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `CHAT_COMPRESS_TOKEN_THRESHOLD` | `64000` | Trigger internal branch compression |
| `CHAT_MAX_CONCURRENT_SEARCHES` | `8` | Max parallel Knowledge tool runs per web worker |
| `WEB_WORKERS` | `4` | Uvicorn worker processes for ASGI |
| `DB_CONN_MAX_AGE` | `60` | Postgres connection reuse (seconds) |
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
| GET | `/api/workspace/<workspace_name>/preprocess-status/` |
| POST | `/api/workspace/preprocess/<workspace_name>/` |
| GET | `/api/knowledge-graph/` |
| GET | `/api/knowledge-graph/entity-types/` |
| GET | `/api/knowledge/entities/search/` |
| GET | `/api/knowledge/entity/<uuid>/` |
| GET | `/api/knowledge/relation/<uuid>/` |
| GET | `/api/knowledge/chunk/<uuid>/` |
| GET | `/api/knowledge/document/<uuid>/` |
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
