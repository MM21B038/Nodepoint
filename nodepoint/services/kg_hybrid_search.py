from __future__ import annotations

import re
from typing import Any

from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz

from nodepoint.backend.vector import get_agent
from nodepoint.quadrant.manager import search_by_workspaces
from nodepoint.services import kg_search
from nodepoint.services.group_scope import GroupSearchScope, filter_records_by_scope
from nodepoint.services.kg_records import record_search_text

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def normalize_weights(
    semantic_weight: float,
    lexical_weight: float,
) -> tuple[float, float]:
    sem = max(0.0, float(semantic_weight))
    lex = max(0.0, float(lexical_weight))
    total = sem + lex
    if total <= 0:
        return 0.6, 0.4
    return sem / total, lex / total


def _min_max_normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    nums = list(values.values())
    lo, hi = min(nums), max(nums)
    if hi <= lo:
        return {k: 1.0 if v > 0 else 0.0 for k, v in values.items()}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def search_text_for_record(rec: dict[str, Any]) -> str:
    kind = rec.get("kind", "entity")
    if kind == "entity":
        return record_search_text(
            "entity",
            name=rec.get("name", ""),
            entity_type=rec.get("entity_type"),
            attributes=rec.get("attributes"),
        )
    if kind == "relation":
        return record_search_text(
            "relation",
            source=rec.get("source", ""),
            target=rec.get("target", ""),
            type_description=rec.get("type_description"),
            description=rec.get("description"),
        )
    if kind == "chunk":
        return record_search_text(
            "chunk",
            file_name=rec.get("file_name", ""),
            chunk_index=int(rec.get("chunk_index") or 0),
            text=rec.get("content") or rec.get("snippet") or "",
        )
    return rec.get("content") or ""


def rerank_records(
    query: str,
    records: list[dict[str, Any]],
    *,
    semantic_weight: float = 0.6,
    lexical_weight: float = 0.4,
    bm25_weight: float = 0.5,
) -> list[dict[str, Any]]:
    if not records:
        return []

    sem_w, lex_w = normalize_weights(semantic_weight, lexical_weight)
    bm25_w = min(1.0, max(0.0, float(bm25_weight)))
    fuzzy_w = 1.0 - bm25_w

    query_tokens = tokenize(query)
    corpus_tokens = [tokenize(search_text_for_record(r)) for r in records]
    if query_tokens and any(corpus_tokens):
        bm25 = BM25Okapi(corpus_tokens)
        bm25_raw = {records[i]["id"]: float(bm25.get_scores(query_tokens)[i]) for i in range(len(records))}
    else:
        bm25_raw = {r["id"]: 0.0 for r in records}

    fuzzy_raw: dict[str, float] = {}
    for rec in records:
        text = search_text_for_record(rec)
        fuzzy_raw[rec["id"]] = fuzz.token_set_ratio(query, text) / 100.0

    sem_raw = {r["id"]: float(r.get("score") or 0.0) for r in records}
    bm25_norm = _min_max_normalize(bm25_raw)
    fuzzy_norm = _min_max_normalize(fuzzy_raw)
    sem_norm = _min_max_normalize(sem_raw)

    for rec in records:
        rid = rec["id"]
        bm25_n = bm25_norm.get(rid, 0.0)
        fuzzy_n = fuzzy_norm.get(rid, 0.0)
        sem_n = sem_norm.get(rid, 0.0)
        lexical = bm25_w * bm25_n + fuzzy_w * fuzzy_n
        total = sem_w * sem_n + lex_w * lexical
        rec["scores"] = {
            "total": round(total, 4),
            "semantic": round(sem_n, 4),
            "bm25": round(bm25_n, 4),
            "fuzzy": round(fuzzy_n, 4),
            "lexical": round(lexical, 4),
        }
        rec["score"] = total

    records.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return records


def hybrid_search(
    query: str,
    workspaces: list[str],
    *,
    limit: int = 10,
    record_type: str | None = None,
    candidate_limit: int = 4000,
    semantic_weight: float = 0.6,
    lexical_weight: float = 0.4,
    bm25_weight: float = 0.5,
    scope: GroupSearchScope | None = None,
) -> list[dict[str, Any]]:
    vector = get_agent().vector(query).squeeze().tolist()
    hits = search_by_workspaces(
        vector,
        workspaces,
        limit=candidate_limit,
        type_filter=record_type,
    )
    records = kg_search.resolve_hits(hits)
    if scope is not None and not scope.is_workspace_tag:
        records = filter_records_by_scope(records, scope)
    reranked = rerank_records(
        query,
        records,
        semantic_weight=semantic_weight,
        lexical_weight=lexical_weight,
        bm25_weight=bm25_weight,
    )
    return reranked[:limit]
