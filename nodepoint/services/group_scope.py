from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.db.models import Q

from nodepoint.enums import GroupTag
from nodepoint.models import (
    GroupDocumentMembership,
    GroupEntityMembership,
    GroupRelationMembership,
    WorkspaceGroup,
    WorkspaceGroupMembership,
)


@dataclass(frozen=True)
class GroupSearchScope:
    group_name: str
    tag: str
    workspace_names: list[str]
    document_ids: list[uuid.UUID]
    entity_ids: list[uuid.UUID]
    relation_ids: list[uuid.UUID]

    @property
    def is_workspace_tag(self) -> bool:
        return self.tag == GroupTag.WORKSPACE

    @property
    def is_empty(self) -> bool:
        if self.tag == GroupTag.WORKSPACE:
            return not self.workspace_names
        if self.tag == GroupTag.FILES:
            return not self.document_ids
        if self.tag == GroupTag.ENTITY:
            return not self.entity_ids
        if self.tag == GroupTag.RELATION:
            return not self.relation_ids
        return True


def build_group_search_scope(group: WorkspaceGroup) -> GroupSearchScope:
    if group.tag == GroupTag.WORKSPACE:
        from nodepoint.services.workspace_group import (
            GROUP_CHAT_PREFIX,
            LEGACY_FLAGGED_CHAT_WORKSPACE_NAME,
        )

        workspace_names = list(
            WorkspaceGroupMembership.objects.filter(group=group)
            .select_related("workspace")
            .exclude(workspace__name__startswith=GROUP_CHAT_PREFIX)
            .exclude(workspace__name=LEGACY_FLAGGED_CHAT_WORKSPACE_NAME)
            .order_by("created_at", "workspace__name")
            .values_list("workspace__name", flat=True)
        )
        return GroupSearchScope(
            group_name=group.name,
            tag=group.tag,
            workspace_names=workspace_names,
            document_ids=[],
            entity_ids=[],
            relation_ids=[],
        )
    if group.tag == GroupTag.FILES:
        doc_rows = (
            GroupDocumentMembership.objects.filter(group=group)
            .select_related("document__workspace")
            .order_by("document_id")
        )
        document_ids = [m.document_id for m in doc_rows]
        workspace_names = sorted(
            {m.document.workspace.name for m in doc_rows},
            key=str.lower,
        )
        return GroupSearchScope(
            group_name=group.name,
            tag=group.tag,
            workspace_names=workspace_names,
            document_ids=document_ids,
            entity_ids=[],
            relation_ids=[],
        )
    if group.tag == GroupTag.ENTITY:
        entity_rows = (
            GroupEntityMembership.objects.filter(group=group)
            .select_related("entity__document__workspace")
            .order_by("entity_id")
        )
        entity_ids = [m.entity_id for m in entity_rows]
        workspace_names = sorted(
            {m.entity.document.workspace.name for m in entity_rows},
            key=str.lower,
        )
        return GroupSearchScope(
            group_name=group.name,
            tag=group.tag,
            workspace_names=workspace_names,
            document_ids=[],
            entity_ids=entity_ids,
            relation_ids=[],
        )
    relation_rows = (
        GroupRelationMembership.objects.filter(group=group)
        .select_related("relation__document__workspace")
        .order_by("relation_id")
    )
    relation_ids = [m.relation_id for m in relation_rows]
    workspace_names = sorted(
        {m.relation.document.workspace.name for m in relation_rows},
        key=str.lower,
    )
    return GroupSearchScope(
        group_name=group.name,
        tag=group.tag,
        workspace_names=workspace_names,
        document_ids=[],
        entity_ids=[],
        relation_ids=relation_ids,
    )


def resolve_group_search_scope(group_name: str) -> GroupSearchScope:
    from nodepoint.services import workspace_group as group_svc

    group = group_svc.get_group_by_name(group_name)
    return build_group_search_scope(group)


def resolve_active_group_search_scope() -> GroupSearchScope | None:
    from nodepoint.services.chat_context import get_group_scope_chat

    group_name = get_group_scope_chat()
    if not group_name:
        return None
    return resolve_group_search_scope(group_name)


def filter_records_by_scope(
    records: list[dict],
    scope: GroupSearchScope,
) -> list[dict]:
    if scope.is_workspace_tag:
        return records

    allowed_docs = {str(doc_id) for doc_id in scope.document_ids}
    allowed_entities = {str(entity_id) for entity_id in scope.entity_ids}
    allowed_relations = {str(relation_id) for relation_id in scope.relation_ids}
    filtered: list[dict] = []

    for rec in records:
        kind = rec.get("kind")
        rid = str(rec.get("id", ""))
        doc_id = str(rec.get("document_id", ""))

        if scope.tag == GroupTag.FILES:
            if doc_id in allowed_docs:
                filtered.append(rec)
            continue

        if scope.tag == GroupTag.ENTITY:
            if kind == "entity" and rid in allowed_entities:
                filtered.append(rec)
            elif kind == "relation":
                source_id = str(rec.get("source_id", ""))
                target_id = str(rec.get("target_id", ""))
                if source_id in allowed_entities or target_id in allowed_entities:
                    filtered.append(rec)
            elif kind == "chunk" and doc_id:
                if _document_has_entity_members(doc_id, scope.entity_ids):
                    filtered.append(rec)
            continue

        if scope.tag == GroupTag.RELATION:
            if kind == "relation" and rid in allowed_relations:
                filtered.append(rec)
            elif kind == "entity" and _entity_in_allowed_relations(rid, scope):
                filtered.append(rec)
            elif kind == "chunk" and doc_id and _document_has_allowed_relation(
                doc_id, scope
            ):
                filtered.append(rec)

    return filtered


def _document_has_entity_members(document_id: str, entity_ids: list[uuid.UUID]) -> bool:
    from nodepoint.models import KnowledgeEntity

    if not entity_ids:
        return False
    return KnowledgeEntity.objects.filter(
        id__in=entity_ids,
        document_id=document_id,
    ).exists()


def _entity_in_allowed_relations(entity_id: str, scope: GroupSearchScope) -> bool:
    from nodepoint.models import KnowledgeRelation

    if not scope.relation_ids:
        return False
    return KnowledgeRelation.objects.filter(
        id__in=scope.relation_ids,
    ).filter(Q(source_id=entity_id) | Q(target_id=entity_id)).exists()


def _document_has_allowed_relation(document_id: str, scope: GroupSearchScope) -> bool:
    from nodepoint.models import KnowledgeRelation

    if not scope.relation_ids:
        return False
    return KnowledgeRelation.objects.filter(
        id__in=scope.relation_ids,
        document_id=document_id,
    ).exists()


def record_allowed_in_scope(rec: dict, scope: GroupSearchScope) -> bool:
    return bool(filter_records_by_scope([rec], scope))
