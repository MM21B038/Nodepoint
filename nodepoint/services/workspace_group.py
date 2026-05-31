from __future__ import annotations

import os
import re
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Count, QuerySet

from nodepoint.enums import GroupTag
from nodepoint.models import (
    Document,
    GroupDocumentMembership,
    GroupEntityMembership,
    GroupRelationMembership,
    KnowledgeEntity,
    KnowledgeRelation,
    Workspace,
    WorkspaceGroup,
    WorkspaceGroupMembership,
)
from nodepoint.services import optional_fields as opt

GROUP_CHAT_PREFIX = "__group_chat__"
LEGACY_FLAGGED_CHAT_WORKSPACE_NAME = "__flagged_chat__"

_GROUP_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,254}$")
_VALID_GROUP_TAGS = frozenset(GroupTag.values)


class GroupError(ValueError):
    pass


class GroupNotFoundError(GroupError):
    pass


def group_chat_workspace_name(group_name: str) -> str:
    return f"{GROUP_CHAT_PREFIX}{group_name}"


def is_internal_chat_workspace_name(name: str) -> bool:
    return name.startswith(GROUP_CHAT_PREFIX) or name == LEGACY_FLAGGED_CHAT_WORKSPACE_NAME


def validate_group_tag(tag: str | None) -> str:
    if tag is None or not str(tag).strip():
        return GroupTag.WORKSPACE
    cleaned = str(tag).strip().lower()
    if cleaned not in _VALID_GROUP_TAGS:
        raise GroupError(
            f"Invalid group tag: {cleaned}. "
            f"Must be one of: {', '.join(sorted(_VALID_GROUP_TAGS))}"
        )
    return cleaned


def normalize_description(description: str | None) -> str:
    try:
        return opt.normalize_description(description)
    except ValueError as exc:
        raise GroupError(str(exc)) from exc


def optional_field_for_api(value: str) -> str | None:
    return opt.optional_field_for_api(value)


def validate_group_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise GroupError("Group name is required")
    if not _GROUP_NAME_RE.match(cleaned):
        raise GroupError(
            "Group name must start with a letter or digit and contain only "
            "letters, digits, underscores, and hyphens"
        )
    return cleaned


def get_group_by_name(name: str) -> WorkspaceGroup:
    try:
        return WorkspaceGroup.objects.get(name=name)
    except WorkspaceGroup.DoesNotExist as exc:
        raise GroupNotFoundError(f"Group not found: {name}") from exc


def get_or_create_group(name: str) -> tuple[WorkspaceGroup, bool]:
    cleaned = validate_group_name(name)
    return WorkspaceGroup.objects.get_or_create(
        name=cleaned,
        defaults={"tag": GroupTag.WORKSPACE},
    )


def _require_group_tag(group: WorkspaceGroup, expected: str) -> None:
    if group.tag != expected:
        raise GroupError(
            f"Group '{group.name}' is tag '{group.tag}'; "
            f"this operation requires tag '{expected}'"
        )


def get_group_member_count(group: WorkspaceGroup) -> int:
    if group.tag == GroupTag.WORKSPACE:
        return group.memberships.count()
    if group.tag == GroupTag.FILES:
        return group.document_memberships.count()
    if group.tag == GroupTag.ENTITY:
        return group.entity_memberships.count()
    if group.tag == GroupTag.RELATION:
        return group.relation_memberships.count()
    return 0


def create_group(
    name: str,
    *,
    tag: str | None = None,
    description: str | None = None,
) -> WorkspaceGroup:
    cleaned = validate_group_name(name)
    if WorkspaceGroup.objects.filter(name=cleaned).exists():
        raise GroupError(f"Group already exists: {cleaned}")
    return WorkspaceGroup.objects.create(
        name=cleaned,
        tag=validate_group_tag(tag),
        description=normalize_description(description),
    )


def _member_count_from_annotated(group: WorkspaceGroup) -> int:
    if group.tag == GroupTag.WORKSPACE:
        return group.workspace_member_count
    if group.tag == GroupTag.FILES:
        return group.document_member_count
    if group.tag == GroupTag.ENTITY:
        return group.entity_member_count
    return group.relation_member_count


def _serialize_group_list_row(group: WorkspaceGroup) -> dict:
    return {
        "name": group.name,
        "tag": group.tag,
        "description": optional_field_for_api(group.description),
        "member_count": _member_count_from_annotated(group),
        "created_at": group.created_at,
    }


def list_groups(
    *,
    tag_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    from nodepoint.services.workspace_catalog import paginate_queryset

    qs = WorkspaceGroup.objects.annotate(
        workspace_member_count=Count("memberships", distinct=True),
        document_member_count=Count("document_memberships", distinct=True),
        entity_member_count=Count("entity_memberships", distinct=True),
        relation_member_count=Count("relation_memberships", distinct=True),
    ).order_by("name")
    if tag_filter:
        qs = qs.filter(tag=validate_group_tag(tag_filter))
    page_groups, pagination = paginate_queryset(qs, page=page, page_size=page_size)
    return {
        "groups": [_serialize_group_list_row(g) for g in page_groups],
        "pagination": pagination,
    }


def _serialize_workspace_member(m: WorkspaceGroupMembership) -> dict:
    return {
        "name": m.workspace.name,
        "created_at": m.workspace.created_at,
    }


def _serialize_document_member(m: GroupDocumentMembership) -> dict:
    return {
        "document_id": str(m.document_id),
        "workspace": m.document.workspace.name,
        "file_name": m.document.file_name,
        "created_at": m.created_at,
    }


def _serialize_entity_member(m: GroupEntityMembership) -> dict:
    return {
        "entity_id": str(m.entity_id),
        "name": m.entity.name,
        "entity_type": m.entity.entity_type,
        "workspace": m.entity.document.workspace.name,
        "created_at": m.created_at,
    }


def _serialize_relation_member(m: GroupRelationMembership) -> dict:
    return {
        "relation_id": str(m.relation_id),
        "source": m.relation.source.name,
        "target": m.relation.target.name,
        "type_description": m.relation.type_description,
        "workspace": m.relation.document.workspace.name,
        "created_at": m.created_at,
    }


def _paginate_group_members(group: WorkspaceGroup, *, page: int, page_size: int):
    from nodepoint.services.workspace_catalog import paginate_queryset

    if group.tag == GroupTag.WORKSPACE:
        qs = (
            WorkspaceGroupMembership.objects.filter(group=group)
            .select_related("workspace")
            .order_by("workspace__name")
        )
        page_rows, pagination = paginate_queryset(qs, page=page, page_size=page_size)
        members = [_serialize_workspace_member(m) for m in page_rows]
    elif group.tag == GroupTag.FILES:
        qs = (
            GroupDocumentMembership.objects.filter(group=group)
            .select_related("document__workspace")
            .order_by("document__workspace__name", "document__file_name")
        )
        page_rows, pagination = paginate_queryset(qs, page=page, page_size=page_size)
        members = [_serialize_document_member(m) for m in page_rows]
    elif group.tag == GroupTag.ENTITY:
        qs = (
            GroupEntityMembership.objects.filter(group=group)
            .select_related("entity__document__workspace")
            .order_by("entity__name")
        )
        page_rows, pagination = paginate_queryset(qs, page=page, page_size=page_size)
        members = [_serialize_entity_member(m) for m in page_rows]
    else:
        qs = (
            GroupRelationMembership.objects.filter(group=group)
            .select_related(
                "relation__source",
                "relation__target",
                "relation__document__workspace",
            )
            .order_by("relation__source__name")
        )
        page_rows, pagination = paginate_queryset(qs, page=page, page_size=page_size)
        members = [_serialize_relation_member(m) for m in page_rows]
    return members, pagination


def list_group_members(
    name: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    group = get_group_by_name(name)
    members, pagination = _paginate_group_members(
        group, page=page, page_size=page_size
    )
    return {
        "group": group.name,
        "tag": group.tag,
        "member_count": get_group_member_count(group),
        "members": members,
        "pagination": pagination,
    }


def get_group_detail(
    name: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    group = get_group_by_name(name)
    members, pagination = _paginate_group_members(
        group, page=page, page_size=page_size
    )
    return {
        "name": group.name,
        "tag": group.tag,
        "description": optional_field_for_api(group.description),
        "created_at": group.created_at,
        "member_count": get_group_member_count(group),
        "members": members,
        "pagination": pagination,
    }


def list_group_members_summary(group_name: str, *, limit: int = 50) -> list[dict]:
    group = get_group_by_name(group_name)
    if group.tag == GroupTag.WORKSPACE:
        qs = (
            WorkspaceGroupMembership.objects.filter(group=group)
            .select_related("workspace")
            .order_by("workspace__name")[:limit]
        )
        return [_serialize_workspace_member(m) for m in qs]
    if group.tag == GroupTag.FILES:
        qs = (
            GroupDocumentMembership.objects.filter(group=group)
            .select_related("document__workspace")
            .order_by("document__workspace__name", "document__file_name")[:limit]
        )
        return [_serialize_document_member(m) for m in qs]
    if group.tag == GroupTag.ENTITY:
        qs = (
            GroupEntityMembership.objects.filter(group=group)
            .select_related("entity__document__workspace")
            .order_by("entity__name")[:limit]
        )
        return [_serialize_entity_member(m) for m in qs]
    qs = (
        GroupRelationMembership.objects.filter(group=group)
        .select_related(
            "relation__source",
            "relation__target",
            "relation__document__workspace",
        )
        .order_by("relation__source__name")[:limit]
    )
    return [_serialize_relation_member(m) for m in qs]


def group_workspaces_summary(group_name: str) -> dict:
    from nodepoint.services.group_scope import resolve_group_search_scope

    group = get_group_by_name(group_name)
    scope = resolve_group_search_scope(group_name)
    return {
        "group": group_name,
        "tag": group.tag,
        "count": len(scope.workspace_names),
        "workspaces": scope.workspace_names,
    }


def _assert_user_workspace(workspace: Workspace) -> None:
    if is_internal_chat_workspace_name(workspace.name):
        raise GroupError("Internal chat workspaces cannot be added to a group")


def _assert_user_document(document: Document) -> None:
    _assert_user_workspace(document.workspace)


def add_workspace_to_group(group_name: str, workspace: Workspace) -> None:
    _assert_user_workspace(workspace)
    group = get_group_by_name(group_name)
    _require_group_tag(group, GroupTag.WORKSPACE)
    WorkspaceGroupMembership.objects.get_or_create(group=group, workspace=workspace)


def remove_workspace_from_group(group_name: str, workspace: Workspace) -> None:
    group = get_group_by_name(group_name)
    _require_group_tag(group, GroupTag.WORKSPACE)
    deleted, _ = WorkspaceGroupMembership.objects.filter(
        group=group, workspace=workspace
    ).delete()
    if not deleted:
        raise GroupError(f"Workspace {workspace.name} is not in group {group_name}")


def add_document_to_group(group_name: str, document: Document) -> None:
    _assert_user_document(document)
    group = get_group_by_name(group_name)
    _require_group_tag(group, GroupTag.FILES)
    GroupDocumentMembership.objects.get_or_create(group=group, document=document)


def remove_document_from_group(group_name: str, document_id: uuid.UUID) -> None:
    group = get_group_by_name(group_name)
    _require_group_tag(group, GroupTag.FILES)
    deleted, _ = GroupDocumentMembership.objects.filter(
        group=group, document_id=document_id
    ).delete()
    if not deleted:
        raise GroupError(f"Document {document_id} is not in group {group_name}")


def add_entity_to_group(group_name: str, entity: KnowledgeEntity) -> None:
    _assert_user_document(entity.document)
    group = get_group_by_name(group_name)
    _require_group_tag(group, GroupTag.ENTITY)
    GroupEntityMembership.objects.get_or_create(group=group, entity=entity)


def remove_entity_from_group(group_name: str, entity_id: uuid.UUID) -> None:
    group = get_group_by_name(group_name)
    _require_group_tag(group, GroupTag.ENTITY)
    deleted, _ = GroupEntityMembership.objects.filter(
        group=group, entity_id=entity_id
    ).delete()
    if not deleted:
        raise GroupError(f"Entity {entity_id} is not in group {group_name}")


def add_relation_to_group(group_name: str, relation: KnowledgeRelation) -> None:
    _assert_user_document(relation.document)
    group = get_group_by_name(group_name)
    _require_group_tag(group, GroupTag.RELATION)
    GroupRelationMembership.objects.get_or_create(group=group, relation=relation)


def remove_relation_from_group(group_name: str, relation_id: uuid.UUID) -> None:
    group = get_group_by_name(group_name)
    _require_group_tag(group, GroupTag.RELATION)
    deleted, _ = GroupRelationMembership.objects.filter(
        group=group, relation_id=relation_id
    ).delete()
    if not deleted:
        raise GroupError(f"Relation {relation_id} is not in group {group_name}")


def delete_group(name: str) -> None:
    group = get_group_by_name(name)
    WorkspaceGroupMembership.objects.filter(group=group).delete()
    GroupDocumentMembership.objects.filter(group=group).delete()
    GroupEntityMembership.objects.filter(group=group).delete()
    GroupRelationMembership.objects.filter(group=group).delete()
    group.delete()


def serialize_group_for_api(group: WorkspaceGroup) -> dict:
    return {
        "name": group.name,
        "tag": group.tag,
        "description": optional_field_for_api(group.description),
        "member_count": get_group_member_count(group),
        "created_at": group.created_at,
    }


def _rename_group_chat_workspace(old_group_name: str, new_group_name: str) -> None:
    from nodepoint.services.workspace import move_workspace_media_dir

    old_chat = group_chat_workspace_name(old_group_name)
    new_chat = group_chat_workspace_name(new_group_name)
    try:
        chat_workspace = Workspace.objects.get(name=old_chat)
    except Workspace.DoesNotExist:
        return

    if chat_workspace.name == new_chat:
        return

    if Workspace.objects.filter(name=new_chat).exclude(pk=chat_workspace.pk).exists():
        raise GroupError(
            f"Cannot rename group: chat workspace '{new_chat}' already exists"
        )

    move_workspace_media_dir(old_chat, new_chat)
    chat_workspace.name = new_chat
    chat_workspace.save(update_fields=["name"])


def update_group(current_name: str, updates: dict) -> WorkspaceGroup:
    allowed = {"name", "description"}
    unknown = set(updates) - allowed
    if unknown:
        raise GroupError(f"Unknown fields: {', '.join(sorted(unknown))}")
    if not updates:
        raise GroupError("No fields to update")

    group = get_group_by_name(current_name)
    old_name = group.name
    update_fields: list[str] = []

    if "name" in updates:
        new_name = validate_group_name(updates["name"])
        if new_name != old_name and WorkspaceGroup.objects.filter(name=new_name).exists():
            raise GroupError(f"Group already exists: {new_name}")
        group.name = new_name
        update_fields.append("name")

    if "description" in updates:
        group.description = normalize_description(updates["description"])
        update_fields.append("description")

    with transaction.atomic():
        group.save(update_fields=update_fields)
        if group.name != old_name:
            _rename_group_chat_workspace(old_name, group.name)

    return group


def get_group_workspaces_qs(group_name: str) -> QuerySet[Workspace]:
    from nodepoint.services.group_scope import resolve_group_search_scope

    group = get_group_by_name(group_name)
    if group.tag == GroupTag.WORKSPACE:
        return (
            Workspace.objects.filter(group_memberships__group=group)
            .exclude(name__startswith=GROUP_CHAT_PREFIX)
            .exclude(name=LEGACY_FLAGGED_CHAT_WORKSPACE_NAME)
            .order_by("created_at")
            .distinct()
        )
    scope = resolve_group_search_scope(group_name)
    if not scope.workspace_names:
        return Workspace.objects.none()
    return (
        user_workspaces_qs()
        .filter(name__in=scope.workspace_names)
        .order_by("created_at")
    )


def list_group_workspace_names(group_name: str) -> list[str]:
    from nodepoint.services.group_scope import resolve_group_search_scope

    return resolve_group_search_scope(group_name).workspace_names


def get_group_document_ids(group_name: str) -> list[uuid.UUID]:
    group = get_group_by_name(group_name)
    if group.tag != GroupTag.FILES:
        return []
    return list(
        GroupDocumentMembership.objects.filter(group=group).values_list(
            "document_id", flat=True
        )
    )


def get_group_entity_ids(group_name: str) -> list[uuid.UUID]:
    group = get_group_by_name(group_name)
    if group.tag != GroupTag.ENTITY:
        return []
    return list(
        GroupEntityMembership.objects.filter(group=group).values_list(
            "entity_id", flat=True
        )
    )


def get_group_relation_ids(group_name: str) -> list[uuid.UUID]:
    group = get_group_by_name(group_name)
    if group.tag != GroupTag.RELATION:
        return []
    return list(
        GroupRelationMembership.objects.filter(group=group).values_list(
            "relation_id", flat=True
        )
    )


def get_or_create_group_chat_workspace(group_name: str) -> Workspace:
    get_group_by_name(group_name)
    chat_name = group_chat_workspace_name(group_name)
    workspace, _created = Workspace.objects.get_or_create(name=chat_name)
    workspace_path = os.path.join(
        settings.MEDIA_ROOT,
        "workspaces",
        workspace.name,
    )
    os.makedirs(workspace_path, exist_ok=True)
    return workspace


def get_default_upload_workspace() -> Workspace | None:
    """First user workspace by created_at (for uploads without workspace_name)."""
    return user_workspaces_qs().order_by("created_at").first()


def user_workspaces_qs() -> QuerySet[Workspace]:
    return (
        Workspace.objects.exclude(name__startswith=GROUP_CHAT_PREFIX)
        .exclude(name=LEGACY_FLAGGED_CHAT_WORKSPACE_NAME)
    )
