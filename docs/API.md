# Nodepoint API Reference

Complete reference for REST and WebSocket APIs. Base URL example: `http://localhost:8000`.

---

## Table of contents

1. [Overview](#overview)
2. [Conventions](#conventions)
3. [Quick reference — all REST endpoints](#quick-reference--all-rest-endpoints)
4. [Shared scope: `workspace_name` vs `group`](#shared-scope-workspace_name-vs-group)
5. [Workspace](#workspace)
6. [Documents](#documents)
7. [Preprocess](#preprocess)
8. [Knowledge graph](#knowledge-graph)
9. [Knowledge records (REST)](#knowledge-records-rest)
10. [Chat (REST)](#chat-rest)
11. [Chat (WebSocket)](#chat-websocket)
12. [Agent tools](#agent-tools)
13. [Environment](#environment)
14. [Endpoint index](#endpoint-index)

---

## Overview

| Surface | Prefix | Purpose |
|---------|--------|---------|
| REST | `/api/` | CRUD for workspaces, documents, graphs, chat, groups |
| WebSocket | `/ws/chat/group/<name>/`, `/ws/chat/<workspace_name>/` | Live agent streaming (token-by-token) |
| Admin | `/admin/` | Django admin |
| Media (DEBUG) | `/media/` | Uploaded files |

```text
Group-scope (cross-workspace)                      Per-workspace
  GET  /api/workspace/stats/
  GET  /api/workspace/page/?group=<name>
  GET  /api/knowledge-graph/entity-types/?group=<name>
  GET  /api/knowledge-graph/?group=<name>
  GET  /api/knowledge/entities/search/?group=<name>
  GET  /api/group/list/
  GET  /api/chat/group/<name>/                       GET  /api/chat/<name>/
  WS   /ws/chat/group/<name>/                        WS   /ws/chat/<name>/
```

### Workspace groups

Create named groups and assign workspaces (many-to-many). Use **`group=<name>`** on KG, entity search, chat summary, and group chat.

| Use | API |
|-----|-----|
| Create group | `POST /api/group/create/` body `{ "name": "research" }` |
| List groups | `GET /api/group/list/` |
| Group detail | `GET /api/group/<name>/` |
| Add / remove workspace | `POST` / `DELETE` `/api/group/<name>/workspaces/` |
| Delete group | `DELETE /api/group/<name>/` |
| KG / search across group | `?group=<name>` |
| Group chat | `GET/DELETE /api/chat/group/<name>/`, `ws://.../ws/chat/group/<name>/` |

| Use | API |
|-----|-----|
| Workspace totals | `GET /api/workspace/stats/` (`total`, `in_group`, `ungrouped`) |
| Paginated directory | `GET /api/workspace/page/` (optional `?group=<name>`) |
| Chat metadata for members | `GET /api/chat/summary/?group=<name>` |

**Group `limit` / `depth`:** applied **per workspace** in group responses.

### Two chat modes (separate threads)

| Mode | REST | WebSocket | Message storage |
|------|------|-----------|-----------------|
| **Group-scope** | `GET/DELETE /api/chat/group/<name>/` | `/ws/chat/group/<name>/` | Internal `__group_chat__<name>` |
| **Per-workspace** | `GET/DELETE /api/chat/<workspace_name>/` | `/ws/chat/<workspace_name>/` | That workspace’s conversation |

Per-workspace chat searches **only** that workspace (not other group members).

### Upload default

If `workspace_name` is omitted on upload, the file goes to the **oldest** user workspace (`created_at` ascending). Returns `400` if no workspace exists.

### Reserved names

Names starting with `__group_chat__` and legacy `__flagged_chat__` are reserved.

**Authentication — None on these endpoints (add at the gateway if needed).

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
| Entity `vector` | `PENDING`, `COMPLETED`, `FAILED` (Qdrant embedding job state) |
| Chat `role` | `system`, `user`, `assistant`, `tool` |

### Error responses

Most failures return:

```json
{ "error": "Human-readable message" }
```

| HTTP | Typical cause |
|------|----------------|
| `400` | Missing/invalid body or query; both `workspace_name` and `group`; reserved workspace name |
| `404` | Unknown workspace, document, chat, or knowledge record |

---

## Quick reference — all REST endpoints

Base path: `/api/`. All paths below are relative to that prefix.

| Method | Path | Summary |
|--------|------|---------|
| POST | `workspace/create/` | Create workspace + media folder |
| GET | `workspace/list/` | List user workspaces (`groups`, excludes `__flagged_chat__`) |
| GET | `workspace/stats/` | Totals: all, in_group, ungrouped workspace counts |
| GET | `workspace/page/` | Paginated workspaces with file/chunk/entity/relation counts |
| GET | `group/<name>/` | Count and names of group member workspaces |
| DELETE | `workspace/delete/<name>/` | Delete workspace and files |
| POST | `group/create/` | Create workspace group |
| GET | `group/list/` | List groups |
| GET | `group/<name>/` | Group detail |
| POST | `group/<name>/workspaces/` | Add workspace to group |
| DELETE | `group/<name>/workspaces/<workspace_name>/` | Remove from group |
| DELETE | `group/<name>/` | Delete group |
| GET | `chat/group/<name>/` | Group-scoped chat history |
| DELETE | `chat/group/<name>/` | Clear group-scoped chat |
| POST | `document/upload/` | Upload `.txt`/`.md`; queue preprocess pipeline |
| GET | `document/<workspace_name>/` | List documents in workspace |
| DELETE | `document/delete/<workspace_name>/<file_name>/` | Delete one document |
| GET | `workspace/<workspace_name>/preprocess-status/` | Pipeline / vector / chunk status |
| POST | `workspace/preprocess/<workspace_name>/` | Queue full workspace preprocess |
| GET | `knowledge-graph/entity-types/` | Distinct entity types + counts |
| GET | `knowledge-graph/` | Filtered KG subgraph (`entity_type`, `depth`, `limit`) |
| GET | `knowledge/entities/search/` | Fuzzy name search + subgraph |
| GET | `knowledge/entity/<uuid>/` | Entity record JSON |
| GET | `knowledge/relation/<uuid>/` | Relation record JSON |
| GET | `knowledge/chunk/<uuid>/` | Chunk record JSON (Mongo text) |
| GET | `knowledge/document/<uuid>/` | Document text JSON |
| GET | `chat/<workspace_name>/` | Per-workspace chat history |
| DELETE | `chat/<workspace_name>/` | Clear per-workspace chat |
| GET | `chat/summary/` | Message counts / `updated_at` |

WebSocket (not under `/api/`): `ws://<host>/ws/chat/group/<name>/`, `ws://<host>/ws/chat/<workspace_name>/`.

---

## Shared scope: `workspace_name` vs `group`

Several endpoints require **exactly one** scope (not both, not neither):

| Scope | Query | Meaning |
|-------|-------|---------|
| Single workspace | `workspace_name=PRAJNA` | Data from that workspace only |
| Group | `group=<name>` | One result object **per** group member workspace, excluding internal `__group_chat__*` workspaces |

Applies to:

- `GET /api/knowledge-graph/`
- `GET /api/knowledge-graph/entity-types/`
- `GET /api/knowledge/entities/search/`
- `GET /api/chat/summary/`

**Examples**

```bash
# Single workspace
curl "http://localhost:8000/api/knowledge-graph/?workspace_name=PRAJNA&depth=1&limit=100"

# All group member workspaces (per-workspace graphs in arrays)
curl "http://localhost:8000/api/knowledge-graph/?group=<name>&entity_type=PER"

curl "http://localhost:8000/api/knowledge/entities/search/?q=Alice&group=<name>&threshold=0.6"

curl "http://localhost:8000/api/group/<name>/"
```

| Status | Condition |
|--------|-----------|
| `400` | Both `workspace_name` and `group=<name>`, or neither |
| `404` | Unknown `workspace_name` (single-workspace mode only) |

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
| `400` | `name` missing or reserved |

---

### `GET /api/workspace/list/`

Lightweight paginated list (name, groups, `created_at` only — **no** file/entity counts). Same pagination query params as `GET /api/workspace/page/`. For counts use `/api/workspace/page/`.

**Response `200`** — object with `workspaces`, `pagination`, `include_counts` (always `false`). Internal chat workspaces omitted.

```json
{
  "include_counts": false,
  "pagination": { "page": 1, "page_size": 20, "total_items": 522, "total_pages": 27, "has_next": true, "has_previous": false },
  "workspaces": [
    { "name": "PRAJNA", "groups": ["research"], "created_at": "2026-05-15T12:00:00.123456Z" }
  ]
}
```

---

### `GET /api/workspace/stats/`

Aggregate workspace counts (user workspaces only; excludes internal `__flagged_chat__`).

**Request**

```bash
curl "http://localhost:8000/api/workspace/stats/"
```

**Response `200`**

```json
{
  "total": 5,
  "in_group": 2,
  "ungrouped": 3
}
```

| Field | Meaning |
|-------|---------|
| `total` | All user workspaces |
| (removed) | Use `?group=<name>` on workspace page |
| `ungrouped` | Workspaces in no group |

Always `in_group + ungrouped === total`.

---

### `GET /api/workspace/page/`

Paginated workspace list with per-workspace resource counts.

**Query parameters**

| Param | Default | Max | Description |
|-------|---------|-----|-------------|
| `page` | `1` | — | Page number (1-based); out-of-range pages clamp to last page |
| `page_size` | `20` | `100` | Items per page |
| `group` | — | — | Optional: filter to workspaces in this group name |
| `include_counts` | `true` | — | Set `false` for a faster directory view (omits `counts` on each row) |

Counts are computed **only for the current page** (not all workspaces). With 500+ workspaces, use `include_counts=false` when browsing and load counts on demand.

**Request**

```bash
curl "http://localhost:8000/api/workspace/page/?page=1&page_size=20&include_counts=false"
curl "http://localhost:8000/api/workspace/page/?page=1&page_size=10&group=research"
```

**Response `200`**

```json
{
  "group": "research",
  "pagination": {
    "page": 1,
    "page_size": 10,
    "total_items": 2,
    "total_pages": 1,
    "has_next": false,
    "has_previous": false
  },
  "workspaces": [
    {
      "name": "PRAJNA",
      
      "created_at": "2026-05-15T12:00:00.123456Z",
      "counts": {
        "files": 3,
        "chunks": 12,
        "entities": 48,
        "relations": 22
      }
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `counts.files` | Documents in the workspace |
| `counts.chunks` | `DocumentChunk` rows |
| `counts.entities` | `KnowledgeEntity` rows (KG nodes) |
| `counts.relations` | `KnowledgeRelation` rows (KG edges) |

Sorted by `created_at` descending, then `name` ascending.

| Status | Condition |
|--------|-----------|
| `400` | Invalid `page`, `page_size`, or `flag` |

---

### `GET /api/group/<name>/`

Group detail with **paginated** member list (`page`, `page_size`; default page size 20, max 100). `workspace_count` is the full membership total; `workspaces` is only the current page.

**Response `200`**

```json
{
  "name": "research",
  "workspace_count": 522,
  "pagination": { "page": 1, "page_size": 20, "total_items": 522, "total_pages": 27, "has_next": true, "has_previous": false },
  "workspaces": [{ "name": "main", "created_at": "..." }]
}
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


## Documents

Allowed extensions: **`.txt`**, **`.md`**, **`.text`**.

Upload triggers a **4-step global preprocess pipeline** (see [Preprocess](#preprocess)).

### `POST /api/document/upload/`

**Content-Type:** `multipart/form-data`

| Field | Required | Default |
|-------|----------|---------|
| `workspace_name` | no | oldest user workspace (`created_at` ascending) |
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
| `400` | Missing file, bad extension, or no group member workspace when `workspace_name` omitted |
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

**Example**

```bash
curl -X DELETE "http://localhost:8000/api/document/delete/PRAJNA/notes.md"
```

**Response `200`**

```json
{ "message": "Document deleted successfully" }
```

Deletes the Postgres row, related KG rows (cascade), and the file on disk when present.

| Status | Condition |
|--------|-----------|
| `404` | Workspace or document not found |

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

Source of truth: **Postgres** (`KnowledgeEntity`, `KnowledgeRelation`, per `DocumentChunk` when migrated).  
Semantic search: **Qdrant** (vectors on entities, relations, and chunks; used by `Knowledge.search_graph` in chat).

Entities and relations are always stored per **document** under a **named workspace**. Group-scope chat does not create its own KG rows; REST `?group=<name>` reads each group member workspace separately.

**Common `entity_type` values** (from ingest): `PER`, `ORG`, `LOC`, `PROD`, `EVENT`, `TECH`, `VULN`, `MALWARE`, `TOOL`, `IP`, `DOMAIN`, `SUBDOMAIN`, `OTHER`.

**REST surfaces in this section**

| Endpoint | Purpose |
|----------|---------|
| `GET /api/knowledge-graph/entity-types/` | Count entities per type |
| `GET /api/knowledge-graph/` | Subgraph by type and/or BFS `depth` / `limit` |
| `GET /api/knowledge/entities/search/` | Fuzzy name search + subgraph around matches |
| `GET /api/knowledge/{entity,relation,chunk,document}/<uuid>/` | Single record (see [Knowledge records](#knowledge-records-rest)) |

### `GET /api/knowledge-graph/entity-types/`

Provide **exactly one** scope: `workspace_name` or `group=<name>`.

Returns distinct `entity_type` values with entity counts, sorted by count descending (then `type` ascending).

**Single workspace — request**

```bash
curl "http://localhost:8000/api/knowledge-graph/entity-types/?workspace_name=PRAJNA"
```

**Single workspace — response `200`**

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
    { "workspace": "PRAJNA", "entity_types": [{ "type": "PER", "count": 42 }] },
    { "workspace": "research", "entity_types": [] }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `type` | Stored `entity_type` string, or JSON `null` when blank in Postgres |
| `count` | Number of `KnowledgeEntity` rows with that type |

Empty `workspaces: []` when no workspace exists.

| Status | Condition |
|--------|-----------|
| `400` | Both or neither scope param |
| `404` | Unknown `workspace_name` (single-workspace mode) |

---

### `GET /api/knowledge-graph/`

Provide **exactly one** scope: `workspace_name` or `group=<name>`.

Optional filters (same for both modes; applied **per workspace** in group responses):

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
curl "http://localhost:8000/api/knowledge-graph/?group=<name>&entity_type=PER&limit=100"
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
- Empty `graphs: []` — no group member workspaces (internal `__flagged_chat__` excluded).
- Request higher `limit` / `depth` explicitly for larger subgraphs (defaults cap at 500 nodes).

**Relation to chat**

| API | Data |
|-----|------|
| `GET /api/knowledge-graph/?group=<name>` | Filtered Postgres subgraph per group member workspace |
| Flagged chat `Knowledge.search_graph` | Top semantic hits (Qdrant → Postgres → markdown) |

| Status | Condition |
|--------|-----------|
| `400` | Both or neither scope; invalid `depth`/`limit`; empty `entity_type` after parse |
| `404` | Unknown `workspace_name` |

---

### `GET /api/knowledge/entities/search/`

Fuzzy **entity name** search (rapidfuzz: WRatio, partial_ratio, token_set_ratio, plus a broad candidate pool for typos) and a **subgraph** around matches using `depth` and `limit`.

**Scope:** exactly one of `workspace_name` or `group=<name>` (see [Shared scope](#shared-scope-workspace_name-vs-group)).

| Query | Required | Default | Max | Description |
|-------|----------|---------|-----|-------------|
| `q` | yes | — | — | Name query (typos tolerated) |
| `threshold` | no | `0.6` | `1.0` | Min match score (0–1); only entities with `score >= threshold` |
| `match_limit` | no | `20` | `100` | Max seed matches **per workspace** |
| `depth` | no | `1` | `5` | BFS hops from seeds into `graph` **per workspace** |
| `limit` | no | `500` | `5000` | Max nodes in each `graph` **per workspace** |
| `entity_type` | no | — | — | Comma-separated filter on candidates, e.g. `PER,ORG` |

**Scoring:** candidates are gathered via `icontains`, token prefixes, and a name-ordered workspace sample; each name is scored 0–1. Empty `matches` means nothing met `threshold` (try lowering it or shortening `q`).

**Single workspace — request**

```bash
curl "http://localhost:8000/api/knowledge/entities/search/?q=Alcie&workspace_name=PRAJNA&threshold=0.6&depth=1&limit=100"
```

**Single workspace — response `200`**

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
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "name": "Alice",
      "entity_type": "PER",
      "attributes": { "role": "engineer" },
      "document_id": "550e8400-e29b-41d4-a716-446655440000",
      "file_name": "notes.md",
      "vector": "COMPLETED",
      "created_at": "2026-05-15T12:00:00Z",
      "score": 0.8,
      "workspace": "PRAJNA"
    }
  ],
  "graph": {
    "workspace": "PRAJNA",
    "truncated": false,
    "nodes": [
      {
        "id": "660e8400-e29b-41d4-a716-446655440001",
        "name": "Alice",
        "entity_type": "PER",
        "attributes": { "role": "engineer" },
        "document_id": "550e8400-e29b-41d4-a716-446655440000",
        "file_name": "notes.md",
        "vector": "COMPLETED",
        "created_at": "2026-05-15T12:00:00Z"
      },
      {
        "id": "660e8400-e29b-41d4-a716-446655440002",
        "name": "Acme Corp",
        "entity_type": "ORG",
        "attributes": {},
        "document_id": "550e8400-e29b-41d4-a716-446655440000",
        "file_name": "notes.md",
        "vector": "COMPLETED",
        "created_at": "2026-05-15T12:00:01Z"
      }
    ],
    "edges": [
      {
        "id": "770e8400-e29b-41d4-a716-446655440003",
        "source": "Alice",
        "target": "Acme Corp",
        "source_id": "660e8400-e29b-41d4-a716-446655440001",
        "target_id": "660e8400-e29b-41d4-a716-446655440002",
        "type_description": "works at"
      }
    ]
  }
}
```

| Field | Meaning |
|-------|---------|
| `matches` | Seed entities from fuzzy name search (includes `score`) |
| `graph.nodes` / `graph.edges` | BFS subgraph from seed IDs; same node/edge shape as [knowledge-graph](#get-apiknowledge-graph) |
| `graph.truncated` | `true` if BFS hit `limit` |

**Flagged — request**

```bash
curl "http://localhost:8000/api/knowledge/entities/search/?q=Alice&group=<name>&threshold=0.6&depth=1&limit=100"
```

**Flagged — response `200`**

```json
{
  "query": "Alice",
  "filters": {
    "entity_types": null,
    "depth": 1,
    "limit": 100,
    "threshold": 0.6,
    "match_limit": 20
  },
  "workspaces": [
    {
      "workspace": "main",
      "matches": [
        {
          "id": "660e8400-e29b-41d4-a716-446655440001",
          "name": "Alice",
          "entity_type": "PER",
          "score": 1.0,
          "workspace": "main",
          "document_id": "…",
          "file_name": "notes.md",
          "vector": "COMPLETED",
          "created_at": "2026-05-15T12:00:00Z",
          "attributes": {}
        }
      ],
      "graph": {
        "workspace": "main",
        "truncated": false,
        "nodes": [],
        "edges": []
      }
    },
    {
      "workspace": "research",
      "matches": [],
      "graph": {
        "workspace": "research",
        "truncated": false,
        "nodes": [],
        "edges": []
      }
    }
  ]
}
```

Chat tool `Knowledge.search_entity_by_name` uses the same fuzzy matcher when `exact=false` (supports `threshold`, default `0.6`). It returns **markdown** with relations, not this JSON subgraph.

| Status | Condition |
|--------|-----------|
| `400` | Missing `q`; invalid scope; invalid `threshold` / `depth` / `limit` / `match_limit` |
| `404` | Unknown `workspace_name` |

---

## Knowledge records (REST)

Fetch a single KG row by Postgres UUID. Used by UIs and complementary to agent tools `Knowledge.get_*_record`.

| Method | Path |
|--------|------|
| GET | `/api/knowledge/entity/<uuid>/` |
| GET | `/api/knowledge/relation/<uuid>/` |
| GET | `/api/knowledge/chunk/<uuid>/` |
| GET | `/api/knowledge/document/<uuid>/` |

**Optional query:** `workspace_name=<name>` — if the record’s workspace does not match, returns `404` with `{ "error": "…" }` (access check).

---

### `GET /api/knowledge/entity/<uuid>/`

**Example**

```bash
curl "http://localhost:8000/api/knowledge/entity/660e8400-e29b-41d4-a716-446655440001/?workspace_name=PRAJNA"
```

**Response `200`**

```json
{
  "kind": "entity",
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "chunk_id": "880e8400-e29b-41d4-a716-446655440010",
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "file_name": "notes.md",
  "workspace": "PRAJNA",
  "name": "Alice",
  "entity_type": "PER",
  "attributes": { "role": "engineer" },
  "content": "name: Alice\ntype: PER\nattributes: {'role': 'engineer'}"
}
```

| Field | Meaning |
|-------|---------|
| `chunk_id` | Source chunk UUID, or `null` for legacy whole-document entities |
| `content` | Human-readable summary (not full document text) |

| Status | Condition |
|--------|-----------|
| `404` | Invalid UUID, unknown entity, or `workspace_name` mismatch |

---

### `GET /api/knowledge/relation/<uuid>/`

**Response `200`**

```json
{
  "kind": "relation",
  "id": "770e8400-e29b-41d4-a716-446655440003",
  "chunk_id": "880e8400-e29b-41d4-a716-446655440010",
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "file_name": "notes.md",
  "workspace": "PRAJNA",
  "source_entity_id": "660e8400-e29b-41d4-a716-446655440001",
  "target_entity_id": "660e8400-e29b-41d4-a716-446655440002",
  "source": "Alice",
  "target": "Acme Corp",
  "type_description": "works at",
  "description": "Alice works at Acme Corp.",
  "content": "Alice — works at — Acme Corp.\ndescription: Alice works at Acme Corp."
}
```

---

### `GET /api/knowledge/chunk/<uuid>/`

Returns full chunk text from Mongo when available.

**Response `200`**

```json
{
  "kind": "chunk",
  "id": "880e8400-e29b-41d4-a716-446655440010",
  "chunk_id": "880e8400-e29b-41d4-a716-446655440010",
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "file_name": "notes.md",
  "workspace": "PRAJNA",
  "chunk_index": 0,
  "content": "Full chunk text from Mongo chunk_content…"
}
```

---

### `GET /api/knowledge/document/<uuid>/`

Assembled document text: joined chunk bodies, legacy Mongo `Content`, or file on disk.

**Response `200`**

```json
{
  "kind": "doc",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "file_name": "notes.md",
  "workspace": "PRAJNA",
  "content": "Entire document text…"
}
```

**Citations in chat:** use `[entity](id)`, `[relation](id)`, `[chunk](id)`, `[doc](document_id)` — not `[source: file_name]`.

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


### `GET /api/chat/<workspace_name>/`

Lazy-creates that workspace's chat on first access. Unknown or reserved name → `404`.

**Response `200`**

```json
{
  "workspace": "main",
  
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

Same query rules as [Shared scope](#shared-scope-workspace_name-vs-group): `workspace_name` **or** `group=<name>`.

Does not include internal `__flagged_chat__` in group lists. Counts **root-branch** messages only (same as GET chat history).

**Single workspace — request**

```bash
curl "http://localhost:8000/api/chat/summary/?workspace_name=PRAJNA"
```

**Single workspace — response `200`**

```json
{
  "workspace": "PRAJNA",
  
  "updated_at": "2026-05-15T12:30:00Z",
  "message_count": 12
}
```

**Flagged — request**

```bash
curl "http://localhost:8000/api/chat/summary/?group=<name>"
```

**Flagged — response `200`**

```json
{
  "workspaces": [
    {
      "workspace": "PRAJNA",
      
      "updated_at": "2026-05-15T12:30:00Z",
      "message_count": 12
    },
    {
      "workspace": "research",
      
      "updated_at": null,
      "message_count": 0
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `updated_at` | Last message timestamp on root branch, or `null` if only system prompt |
| `message_count` | Messages on root branch (includes system) |

| Status | Condition |
|--------|-----------|
| `400` | Both or neither scope |
| `404` | Unknown `workspace_name` |

---

## Chat (WebSocket)

**Requires:** ASGI server (`uvicorn config.asgi:application`) and Redis (Channels layer).

| URL | Chat mode |
|-----|-----------|
| `ws://<host>/ws/chat/group/<name>/` | Group-scope (cross-workspace search) |
| `ws://<host>/ws/chat/<workspace_name>/` | Single workspace |

Reserved: names starting with `__group_chat__` is not a user workspace name.

### Typical client flow (group-scope)

1. Create a group and add workspaces: `POST /api/group/create/`, `POST /api/group/<name>/workspaces/`
2. `GET /api/knowledge-graph/entity-types/?group=<name>` and `GET /api/knowledge-graph/?group=<name>&entity_type=...` — optional KG UI data
3. `GET /api/chat/group/<name>/` — load group chat history
4. Connect `ws://<host>/ws/chat/group/<name>/` → `chat.ready` (if `agent_busy`, turn still running — live stream auto-attaches)
5. Send `chat.send` → `chat.turn_started` → stream → `chat.done`
6. `GET /api/chat/?group=<name>` again — refresh messages

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

#### Reconnect (live stream attach)

```json
{ "type": "chat.reconnect" }
```

Use after a drop **or** rely on auto-attach: `chat.ready` with `agent_busy: true` already subscribes to the in-flight turn.

**Response (turn running):**

```json
{
  "type": "chat.reconnected",
  "agent_busy": true,
  "turn_id": "...",
  "hint": "Refresh chat history via REST for content received while offline; live stream continues from reconnect."
}
```

**Response (idle):** `{ "type": "chat.reconnected", "agent_busy": false }`

While offline, call `GET /api/chat/<workspace>/` or `GET /api/chat/group/<name>/` once to fill the gap; live tokens resume on the WebSocket from reconnect onward (no token replay).

#### Turn status

```json
{ "type": "chat.status" }
```

**Response:** `{ "type": "chat.status", "agent_busy": true|false, "turn_id": "...", "turn_started_at": "..." }`

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
| `chat.compress_started` | Context compression began | `message` |
| `chat.compress_completed` | Handoff summary generated | `message`, `summary_chars` |
| `chat.compressed` | Context compression (server switched branch internally) | — |
| `chat.compress_failed` | Compression failed; turn continues without new branch | `message` |
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

**Group-scope** (`/ws/chat/group/<name>/`):

```json
{
  "type": "chat.ready",
  "group": "research",
  "workspaces": ["main", "research"]
}
```

| Field | Meaning |
|-------|---------|
| `workspace` | Per-workspace mode only |
| `group` | Group-scope mode only |
| `workspaces` | Workspaces included in `Knowledge.search_graph` for this connection |

Close codes: `4000` invalid URL; `4004` unknown workspace (per-workspace mode).

#### `chat.done` (after each `chat.send` turn)

```json
{ "type": "chat.done" }
```

Persisted messages:

- Group-scope → `GET /api/chat/?group=<name>`
- Per-workspace → `GET /api/chat/<workspace_name>/`

---

### Context compression

When the active thread exceeds **`CHAT_COMPRESS_TOKEN_THRESHOLD`** (default **80000**):

1. Emits `{ "type": "chat.compress_started", "message": "…" }` (UI status; wrapped in `section: compression`).
2. Calls the compression model with **`CHAT_COMPRESS_MAX_OUTPUT_TOKENS`** (default **4000**) and a terse handoff prompt.
3. Emits `{ "type": "chat.compress_completed", "message": "…", "summary_chars": N }`.
4. Creates an **internal** child branch with the handoff report (not shown in REST root history).
5. Switches `active_branch_id` to the new branch and emits `{ "type": "chat.compressed" }`.
6. On failure: `{ "type": "chat.compress_failed", "message": "…" }` and the turn continues without a new branch.

REST history (`messages` on GET chat) is served from the **root** branch. User/assistant/tool
messages are mirrored to root as they are saved (compression handoff text stays internal-only).
On compression, any messages on the parent branch are synced to root before the internal child is created.

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
| `/ws/chat/group/<name>/` | Group member workspaces only |
| `/ws/chat/<name>/` | That workspace only |

If group-scope chat runs and the group has no members, the tool returns plain text:

```text
No workspaces in this group. Add members via group APIs to include them in knowledge search.
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

Returns matches with `score` (fuzzy mode), outgoing/incoming relations (relation ids and peer entity ids). See [entity search REST API](#get-apiknowledgeentitiessearch) for JSON subgraph with `depth` / `limit`.

---

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `CHAT_COMPRESS_TOKEN_THRESHOLD` | `80000` | Trigger internal branch compression |
| `CHAT_COMPRESS_MAX_OUTPUT_TOKENS` | `4000` | Max tokens in handoff report |
| `CHAT_COMPRESS_TEMPERATURE` | `0.2` | Compression LLM temperature |
| `CHAT_COMPRESS_MAX_MESSAGES` | `30` | Max thread messages sent to compression |
| `CHAT_MAX_CONCURRENT_SEARCHES` | `8` | Max parallel Knowledge tool runs per web worker |
| `WEB_WORKERS` | `4` | Uvicorn worker processes for ASGI |
| `DB_CONN_MAX_AGE` | `60` | Postgres connection reuse (seconds) |
| `CHAT_DEFAULT_SYSTEM` | (see settings) | New conversation system prompt |
| `BASE_URL`, `API_KEY` | — | LLM provider for `Agent` |
| `POSTGRES_*`, `MONGO_*`, Redis, Qdrant | — | Data stores (`settings.toml`, `.env`) |

---

## Endpoint index

Alphabetical by path segment. See sections above for full request/response bodies.

### REST

| Method | Path | Section |
|--------|------|---------|
| DELETE | `/api/chat/group/<name>/` | [Chat REST](#delete-apichatflaggedtrue) |
| GET | `/api/chat/group/<name>/` | [Chat REST](#get-apichatflaggedtrue) |
| GET | `/api/chat/<workspace_name>/` | [Chat REST](#get-apichatworkspace_name) |
| DELETE | `/api/chat/<workspace_name>/` | [Chat REST](#delete-apichatworkspace_name) |
| GET | `/api/chat/summary/` | [Chat REST](#get-apichatsummary) |
| DELETE | `/api/document/delete/<workspace_name>/<file_name>/` | [Documents](#delete-apidocumentdeleteworkspace_namefile_name) |
| GET | `/api/document/<workspace_name>/` | [Documents](#get-apidocumentworkspace_name) |
| POST | `/api/document/upload/` | [Documents](#post-apidocumentupload) |
| GET | `/api/knowledge-graph/` | [Knowledge graph](#get-apiknowledge-graph) |
| GET | `/api/knowledge-graph/entity-types/` | [Knowledge graph](#get-apiknowledge-graphentity-types) |
| GET | `/api/knowledge/entities/search/` | [Entity name search](#get-apiknowledgeentitiessearch) |
| GET | `/api/knowledge/chunk/<uuid>/` | [Knowledge records](#get-apiknowledgechunkuuid) |
| GET | `/api/knowledge/document/<uuid>/` | [Knowledge records](#get-apiknowledgedocumentuuid) |
| GET | `/api/knowledge/entity/<uuid>/` | [Knowledge records](#get-apiknowledgeentityuuid) |
| GET | `/api/knowledge/relation/<uuid>/` | [Knowledge records](#get-apiknowledgerelationuuid) |
| POST | `/api/workspace/create/` | [Workspace](#post-apiworkspacecreate) |
| DELETE | `/api/workspace/delete/<name>/` | [Workspace](#delete-apiworkspacedeletename) |
| GET | `/api/group/<name>/` | [Groups](#get-apigroupname) |
| GET | `/api/workspace/list/` | [Workspace](#get-apiworkspacelist) |
| GET | `/api/workspace/page/` | [Workspace](#get-apiworkspacepage) |
| GET | `/api/workspace/stats/` | [Workspace](#get-apiworkspacestats) |
| GET | `/api/workspace/<workspace_name>/preprocess-status/` | [Preprocess](#get-apiworkspaceworkspace_namepreprocess-status) |
| POST | `/api/workspace/preprocess/<workspace_name>/` | [Preprocess](#post-apiworkspacepreprocessworkspace_name) |

### WebSocket

| Direction | Path / event | Section |
|-----------|----------------|---------|
| Connect | `ws://<host>/ws/chat/group/<name>/` | [WebSocket](#chat-websocket) |
| Connect | `ws://<host>/ws/chat/<workspace_name>/` | [WebSocket](#chat-websocket) |
| Client → server | `ping`, `chat.send`, `chat.cancel` | [Client → server](#client--server) |
| Server → client | `chat.ready`, `thinking_token`, `assistant_response_token`, `section`, tools, `chat.compress_*`, `chat.compressed`, `chat.done`, `error`, `pong` | [Server → client](#server--client-message-categories) |

### Agent tools (WebSocket only)

| Tool | Section |
|------|---------|
| `Knowledge.search_graph` | [Agent tools](#knowledgesearch_graph) |
| `Knowledge.get_entity_record` | [Agent tools](#knowledgeget_entity_record--get_relation_record--get_chunk_record--get_document_record) |
| `Knowledge.get_relation_record` | same |
| `Knowledge.get_chunk_record` | same |
| `Knowledge.get_document_record` | same |
| `Knowledge.search_entity_by_name` | [Agent tools](#knowledgesearch_entity_by_name) |
