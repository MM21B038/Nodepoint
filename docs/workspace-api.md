# Workspace knowledge graph and chat summary APIs

REST endpoints for listing Postgres knowledge graphs and conversations per workspace, including bulk listing for flagged workspaces (`Workspace.is_flag == True`).

## Knowledge graph

Scope: provide **exactly one** of `workspace_name=<name>` or `flagged=true` (not both). Missing both → `400`.

### Entity types

`GET /api/knowledge-graph/entity-types/`

Lists distinct `entity_type` values with counts per workspace.

**Single workspace** — `?workspace_name=PRAJNA`

```json
{
  "workspace": "PRAJNA",
  "entity_types": [
    { "type": "PER", "count": 42 },
    { "type": "ORG", "count": 10 }
  ]
}
```

**Flagged** — `?flagged=true`

```json
{
  "workspaces": [
    { "workspace": "PRAJNA", "entity_types": [{ "type": "PER", "count": 42 }] }
  ]
}
```

Sorted by `count` descending. `type` is `null` when the stored value is blank.

### Filtered graph

`GET /api/knowledge-graph/`

| Query | Default | Max | Description |
|-------|---------|-----|-------------|
| `entity_type` | — | — | Comma-separated types, e.g. `PER,ORG` |
| `depth` | `1` | `5` | BFS hops from seed entities |
| `limit` | `500` | `5000` | Max nodes per workspace |

**Seeds:** entities matching `entity_type` when provided; otherwise all entities in the workspace. **BFS:** expand via relations up to `depth` hops, capped at `limit` nodes. **Edges:** relations with both endpoints in the returned node set (includes edges between seeds when `depth=0`).

**Single workspace** — `?workspace_name=PRAJNA&entity_type=PER,ORG&depth=1&limit=500`

```json
{
  "workspace": "PRAJNA",
  "filters": { "entity_types": ["PER", "ORG"], "depth": 1, "limit": 500 },
  "truncated": false,
  "nodes": [
    {
      "id": "...",
      "name": "Alice",
      "entity_type": "PER",
      "attributes": {},
      "document_id": "...",
      "file_name": "doc.md",
      "vector": "pending",
      "created_at": "2026-05-15T12:00:00Z"
    }
  ],
  "edges": [
    {
      "id": "...",
      "source": "Alice",
      "target": "Acme",
      "source_id": "...",
      "target_id": "...",
      "type_description": "works at"
    }
  ]
}
```

**Flagged** — `?flagged=true` (same optional filters; each graph object includes `filters`, `truncated`, `nodes`, `edges`)

```json
{
  "graphs": [
    {
      "workspace": "PRAJNA",
      "filters": { "entity_types": null, "depth": 1, "limit": 500 },
      "truncated": false,
      "nodes": [...],
      "edges": [...]
    }
  ]
}
```

`truncated: true` when seed count or BFS expansion hit `limit`. Flagged scope excludes internal `__flagged_chat__` workspace.

### Fuzzy entity name search

`GET /api/knowledge/entities/search/`

| Query | Default | Description |
|-------|---------|-------------|
| `q` | required | Name to search (fuzzy) |
| `threshold` | `0.6` | Minimum score 0–1 |
| `match_limit` | `20` | Max seed matches |
| `depth` | `1` | Graph BFS hops from seeds |
| `limit` | `500` | Max nodes in `graph` |
| `entity_type` | — | Optional comma-separated filter |

Returns `matches` (ranked entities with `score`) and `graph` (nodes/edges around seeds). Scope: `workspace_name` or `flagged=true`.

## Chat summary

`GET /api/chat/summary/`

Same query rules as the knowledge graph endpoint.

### Single workspace

Query: `workspace_name=<name>`

```json
{
  "workspace": "PRAJNA",
  "is_flag": true,
  "updated_at": "...",
  "message_count": 12
}
```

### Flagged workspaces

Query: `flagged=true`

```json
{
  "workspaces": [
    {
      "workspace": "PRAJNA",
      "is_flag": true,
      "updated_at": "...",
      "message_count": 0
    }
  ]
}
```

Flagged-scope chat: `GET /api/chat/?flagged=true`, `ws://.../ws/chat/flagged/` (separate thread; search uses starred workspaces only). Per-workspace: `GET /api/chat/<workspace_name>/`. See [API.md](../API.md).

## Agent tools (chat)

Chat exposes **only** `Knowledge.search_graph`. The system prompt requires answers from tool output with `[source: file_name]` citations.

| Tool | Parameters | Description |
|------|------------|-------------|
| `Knowledge.search_graph` | `query`, `limit`, `record_type` | Qdrant search → Postgres resolve → markdown document with `## [source: file_name]` sections |

Example tool result:

```markdown
## [source: notes.md]
**Entity** (score 0.8700)
- name: Alice
...
```
