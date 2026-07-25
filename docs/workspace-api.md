# Workspace knowledge graph and chat summary APIs

REST endpoints for listing Postgres knowledge graphs and conversations per workspace or per **workspace group**.

## Per-owner naming (admin / superadmin)

Workspace and group **names are unique per owner**, not globally. When your role can see duplicate names, add **`owner_id`** or **`owner_username`** on every request that uses `workspace_name`, `group`, or a workspace/group name in the path. If ambiguous → **`400`** with `candidates`. See [API.md — Workspace — per-owner naming](API.md#workspace--per-owner-naming) for full rules.

```bash
curl "http://localhost:8000/api/knowledge-graph/?workspace_name=123&owner_id=3"
curl "http://localhost:8000/api/knowledge-graph/entity-types/?group=research&owner_id=3"
curl "http://localhost:8000/api/chat/summary/?workspace_name=123&owner_username=alice"
```

## Knowledge graph

Scope: provide **exactly one** of `workspace_name=<name>` or `group=<name>`. Missing scope → `400`. Use `owner_id` / `owner_username` when the name is ambiguous.

## Workspace groups

Groups have a fixed **`tag`** set at create: `workspace` (default) | `files` | `entity` | `relation`. Each group holds members of that type only.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/group/create/` | Create group `{ "name": "...", "tag": "workspace", "description": "..." }` |
| GET | `/api/group/list/` | Paginated groups (`?tag=`, `?page=`, `?page_size=`, optional `?owner_id=`) |
| GET | `/api/group/lookup/` | Resolve group name to owner(s) for disambiguation (`?name=`, optional `?owner_id=`) |
| GET | `/api/group/<name>/` | Group metadata + paginated typed `members` |
| GET | `/api/group/<name>/members/` | Paginated members only (all tag types) |
| PATCH | `/api/group/<name>/` | Update `{ "name", "description" }` only |
| POST | `/api/group/<name>/workspaces/` | Add workspace (workspace tag) |
| DELETE | `/api/group/<name>/workspaces/<workspace_name>/` | Remove workspace |
| POST | `/api/group/<name>/files/` | Add file (files tag) |
| DELETE | `/api/group/<name>/files/<document_id>/` | Remove file |
| POST | `/api/group/<name>/entities/` | Add entity (entity tag) |
| DELETE | `/api/group/<name>/entities/<entity_id>/` | Remove entity |
| POST | `/api/group/<name>/relations/` | Add relation (relation tag) |
| DELETE | `/api/group/<name>/relations/<relation_id>/` | Remove relation |
| DELETE | `/api/group/<name>/` | Delete group |
| GET | `/api/group/<name>/add-options/` | Paginated resources eligible to add (picker; excludes current members) |
| GET | `/api/workspace/<name>/group-options/` | Paginated workspace-tagged groups where add would succeed (`already_member` flag) |

Cross-owner membership rules (who may add whose resources) are in [API.md — Workspace groups](API.md#workspace-groups).

```bash
# Which user owns "research" when names collide (admin/superadmin UI)
curl "http://localhost:8000/api/group/lookup/?name=research"

# All groups owned by one managed user
curl "http://localhost:8000/api/group/list/?owner_id=7"

# Picker: workspaces not yet in group (admin aggregate group)
curl "http://localhost:8000/api/group/research/add-options/?owner_id=3&search=pra"

# Picker: groups for a workspace (disable rows where already_member is true)
curl "http://localhost:8000/api/workspace/PRAJNA/group-options/?owner_id=3"
```

### Entity types

`GET /api/knowledge-graph/entity-types/`

Lists distinct `entity_type` values with counts per workspace.

**Single workspace** — `?workspace_name=PRAJNA` (add `&owner_id=` if needed)

```json
{
  "workspace": "PRAJNA",
  "entity_types": [
    { "type": "PER", "count": 42 },
    { "type": "ORG", "count": 10 }
  ]
}
```

**Group** — `?group=research` (add `&owner_id=` if needed)

```json
{
  "group": "research",
  "tag": "workspace",
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
| `file_name` | — | — | Comma-separated document file names (exact match) |
| `depth` | `1` | `5` | BFS hops from seed entities |
| `limit` | `500` | `5000` | Max nodes per workspace |

**Seeds:** entities matching `entity_type` and/or `file_name` when provided; otherwise all entities in the workspace. **BFS:** expand via relations up to `depth` hops, capped at `limit` nodes. **Edges:** relations with both endpoints in the returned node set (includes edges between seeds when `depth=0`). **Nodes:** `id`, `name`, `entity_type` only.

**Single workspace** — `?workspace_name=PRAJNA&entity_type=PER,ORG&depth=1&limit=500`

```json
{
  "workspace": "PRAJNA",
  "filters": {
    "entity_types": ["PER", "ORG"],
    "file_names": null,
    "depth": 1,
    "limit": 500
  },
  "truncated": false,
  "nodes": [
    {
      "id": "...",
      "name": "Alice",
      "entity_type": "PER"
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

**Group** — `?group=research` (add `&owner_id=` if needed) (same optional filters; response includes `tag` and `graphs` per member workspace)

| Group `tag` | Graph seeds |
|-------------|-------------|
| `workspace` | All entities in each member workspace (respecting filters) |
| `files` | Entities in member documents only |
| `entity` | Member entities + optional BFS neighbors |
| `relation` | Endpoints of member relations + optional BFS neighbors |

```json
{
  "group": "research",
  "tag": "workspace",
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

`truncated: true` when seed count or BFS expansion hit `limit`. Group scope excludes internal `__group_chat__*` workspaces.

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
| `file_name` | — | Optional comma-separated document file names (exact match) |

Returns `matches` (ranked entities with `score`) and `graph` (nodes/edges around seeds). Scope: `workspace_name` or `group=<name>`. For groups, response includes `tag` and a `workspaces` array (one bucket per workspace); non-`workspace` tags restrict candidates to group members.

## Chat summary

`GET /api/chat/summary/`

Same query rules as the knowledge graph endpoint.

### Single workspace

Query: `workspace_name=<name>` (add `owner_id` / `owner_username` if ambiguous)

```json
{
  "workspace": "PRAJNA",
  "updated_at": "...",
  "message_count": 12
}
```

### Group

Query: `group=<name>` (add `owner_id` / `owner_username` if ambiguous)

```json
{
  "group": "research",
  "tag": "workspace",
  "member_count": 2,
  "group_chat": { "updated_at": "...", "message_count": 5 },
  "workspaces": [
    { "workspace": "PRAJNA", "updated_at": "...", "message_count": 0 }
  ]
}
```

For non-`workspace` tags, `workspaces` is omitted; `member_count` reflects files/entities/relations in the group.

Group chat: `GET /api/chat/group/<name>/`, `ws://.../ws/chat/group/<name>/` (separate thread; search uses group members only). Per-workspace: `GET /api/chat/<workspace_name>/`. See [API.md](../API.md).

## Agent tools (chat)

Chat exposes **only** `Knowledge.search_graph`. The system prompt requires answers from tool output with `[source: file_name]` citations.

| Tool | Parameters | Description |
|------|------------|-------------|
| `Knowledge.search_graph` | `query`, `limit`, `record_type` | Qdrant search → Postgres resolve → markdown document with `## [source: file_name]` sections |

Example tool result:

```markdown
## [source: notes.md]
...
```
