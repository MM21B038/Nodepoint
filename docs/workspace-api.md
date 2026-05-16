# Workspace knowledge graph and chat summary APIs

REST endpoints for listing Postgres knowledge graphs and conversations per workspace, including bulk listing for flagged workspaces (`Workspace.is_flag == True`).

## Knowledge graph

`GET /api/knowledge-graph/`

### Single workspace

Query: `workspace_name=<name>`

Response:

```json
{
  "workspace": "PRAJNA",
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
    { "source": "Alice", "target": "Acme" }
  ]
}
```

Edges use entity **names** only (`source` / `target`). Nodes include full `KnowledgeEntity` fields.

### Flagged workspaces

Query: `flagged=true`

Response:

```json
{
  "graphs": [
    { "workspace": "PRAJNA", "nodes": [...], "edges": [...] },
    { "workspace": "OTHER", "nodes": [...], "edges": [...] }
  ]
}
```

Provide either `workspace_name` or `flagged=true`, not both. Missing both returns `400`.

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
