from __future__ import annotations

import os
import re
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, QuerySet

from nodepoint.enums import GroupTag
from django.contrib.auth import get_user_model

from nodepoint.auth.visibility import visible_groups_qs, visible_workspaces_qs
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

User = get_user_model()
from nodepoint.services import optional_fields as opt

GROUP_CHAT_PREFIX = "__group_chat__"
LEGACY_FLAGGED_CHAT_WORKSPACE_NAME = "__flagged_chat__"

_GROUP_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,254}$")
_VALID_GROUP_TAGS = frozenset(GroupTag.values)


class GroupError(ValueError):
    pass


class GroupNotFoundError(GroupError):
    pass


class GroupMembershipDenied(GroupError):
    """Caller or resource owner cannot add this resource to the group."""
    pass


class AmbiguousGroupError(GroupError):
    def __init__(self, name: str, candidates: list[dict]):
        self.name = name
        self.candidates = candidates
        super().__init__(
            f"Multiple groups named '{name}'; specify owner_id or owner_username"
        )


def group_chat_workspace_name(group: WorkspaceGroup | str, owner_id: int | None = None) -> str:
    if isinstance(group, WorkspaceGroup):
        return f"{GROUP_CHAT_PREFIX}{group.owner_id}__{group.name}"
    if owner_id is None:
        raise GroupError("owner_id required when group is passed by name only")
    return f"{GROUP_CHAT_PREFIX}{owner_id}__{group}"


def parse_group_chat_workspace_name(workspace_name: str) -> tuple[int, str] | None:
    if not workspace_name.startswith(GROUP_CHAT_PREFIX):
        return None
    suffix = workspace_name[len(GROUP_CHAT_PREFIX) :]
    if "__" in suffix:
        owner_part, group_name = suffix.split("__", 1)
        try:
            return int(owner_part), group_name
        except ValueError:
            return None
    return None


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


def _group_owner_candidates(groups: list[WorkspaceGroup]) -> list[dict]:
    return [
        {
            "owner_id": g.owner_id,
            "owner_username": g.owner.username,
            "group_id": g.pk,
        }
        for g in groups
    ]


def get_group_by_name(
    name: str,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> WorkspaceGroup:
    qs = WorkspaceGroup.objects.filter(name=name).select_related("owner")
    if actor is not None:
        qs = qs.filter(pk__in=visible_groups_qs(actor).values_list("pk", flat=True))
    if owner_id is not None:
        qs = qs.filter(owner_id=owner_id)

    matches = list(qs)
    if not matches:
        raise GroupNotFoundError(f"Group not found: {name}")
    if len(matches) > 1:
        raise AmbiguousGroupError(name, _group_owner_candidates(matches))
    group = matches[0]
    if actor is not None:
        from nodepoint.auth.visibility import can_access_group

        if not can_access_group(actor, group):
            raise GroupNotFoundError(f"Group not found: {name}")
    return group


def get_or_create_group(
    name: str, *, owner: User | None = None
) -> tuple[WorkspaceGroup, bool]:
    cleaned = validate_group_name(name)
    defaults = {"tag": GroupTag.WORKSPACE}
    if owner is not None:
        defaults["owner"] = owner
    if owner is None:
        raise GroupError("owner is required")
    group, created = WorkspaceGroup.objects.get_or_create(
        name=cleaned,
        owner=owner,
        defaults=defaults,
    )
    return group, created


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
    owner: User,
    tag: str | None = None,
    description: str | None = None,
) -> WorkspaceGroup:
    cleaned = validate_group_name(name)
    if WorkspaceGroup.objects.filter(owner=owner, name=cleaned).exists():
        raise GroupError(f"Group already exists for this owner: {cleaned}")
    return WorkspaceGroup.objects.create(
        name=cleaned,
        owner=owner,
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
        "id": group.pk,
        "name": group.name,
        "owner_id": group.owner_id,
        "owner_username": group.owner.username,
        "tag": group.tag,
        "description": optional_field_for_api(group.description),
        "member_count": _member_count_from_annotated(group),
        "created_at": group.created_at,
    }


def list_groups(
    *,
    actor: User,
    tag_filter: str | None = None,
    owner_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    from nodepoint.services.workspace_catalog import paginate_queryset

    qs = visible_groups_qs(actor).select_related("owner").annotate(
        workspace_member_count=Count("memberships", distinct=True),
        document_member_count=Count("document_memberships", distinct=True),
        entity_member_count=Count("entity_memberships", distinct=True),
        relation_member_count=Count("relation_memberships", distinct=True),
    ).order_by("owner__username", "name")
    if tag_filter:
        qs = qs.filter(tag=validate_group_tag(tag_filter))
    if owner_id is not None:
        qs = qs.filter(owner_id=owner_id)
    page_groups, pagination = paginate_queryset(qs, page=page, page_size=page_size)
    return {
        "groups": [_serialize_group_list_row(g) for g in page_groups],
        "pagination": pagination,
    }


def lookup_groups_by_name(
    name: str,
    *,
    actor: User,
    owner_id: int | None = None,
    tag_filter: str | None = None,
) -> dict:
    """Return visible groups matching name with owner info (disambiguation picker)."""
    cleaned = validate_group_name(name)
    qs = visible_groups_qs(actor).filter(name=cleaned).select_related("owner").annotate(
        workspace_member_count=Count("memberships", distinct=True),
        document_member_count=Count("document_memberships", distinct=True),
        entity_member_count=Count("entity_memberships", distinct=True),
        relation_member_count=Count("relation_memberships", distinct=True),
    )
    if owner_id is not None:
        qs = qs.filter(owner_id=owner_id)
    if tag_filter:
        qs = qs.filter(tag=validate_group_tag(tag_filter))
    matches = list(qs.order_by("owner__username", "name"))
    if not matches:
        raise GroupNotFoundError(f"Group not found: {cleaned}")
    rows = [_serialize_group_list_row(g) for g in matches]
    return {
        "name": cleaned,
        "ambiguous": len(rows) > 1,
        "matches": rows,
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
    actor: User | None = None,
    owner_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    group = get_group_by_name(name, actor=actor, owner_id=owner_id)
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
    actor: User | None = None,
    owner_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    group = get_group_by_name(name, actor=actor, owner_id=owner_id)
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


def list_group_members_summary(
    group_name: str,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
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


def assert_may_add_to_group(
    *,
    actor: User,
    group: WorkspaceGroup,
    resource_owner_id: int,
) -> None:
    """Cross-owner rules for group membership (see docs/API.md)."""
    from nodepoint.auth.users import user_role
    from nodepoint.auth.visibility import can_access_owner
    from nodepoint.enums import UserRole

    if not can_access_owner(actor, resource_owner_id):
        raise GroupMembershipDenied("You do not have access to this resource")

    if resource_owner_id == group.owner_id:
        return

    role = user_role(actor)
    if role in (UserRole.ADMIN, UserRole.SUPERADMIN) and group.owner_id == actor.pk:
        return

    raise GroupMembershipDenied(
        "Cannot add another user's resource to this group"
    )


def may_add_to_group(
    *,
    actor: User,
    group: WorkspaceGroup,
    resource_owner_id: int,
) -> bool:
    try:
        assert_may_add_to_group(
            actor=actor, group=group, resource_owner_id=resource_owner_id
        )
        return True
    except GroupMembershipDenied:
        return False


def eligible_resource_owner_ids(actor: User, group: WorkspaceGroup) -> list[int] | None:
    """Owner ids whose resources may be listed for add-options on this group."""
    from nodepoint.auth.users import user_role
    from nodepoint.auth.visibility import visible_owner_ids
    from nodepoint.enums import UserRole

    role = user_role(actor)
    if role in (UserRole.ADMIN, UserRole.SUPERADMIN) and group.owner_id == actor.pk:
        return visible_owner_ids(actor)
    return [group.owner_id]


def _enforce_workspace_add(
    *,
    actor: User | None,
    group: WorkspaceGroup,
    workspace: Workspace,
) -> None:
    from nodepoint.auth.visibility import AccessDenied, require_workspace_access

    _assert_user_workspace(workspace)
    if actor is not None:
        try:
            require_workspace_access(actor, workspace)
        except AccessDenied as exc:
            raise GroupNotFoundError(str(exc)) from exc
        assert_may_add_to_group(
            actor=actor, group=group, resource_owner_id=workspace.owner_id
        )


def add_workspace_to_group(
    group_name: str,
    workspace: Workspace,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> None:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    _require_group_tag(group, GroupTag.WORKSPACE)
    if actor is not None:
        _enforce_workspace_add(actor=actor, group=group, workspace=workspace)
    else:
        _assert_user_workspace(workspace)
    WorkspaceGroupMembership.objects.get_or_create(group=group, workspace=workspace)


def remove_workspace_from_group(
    group_name: str,
    workspace: Workspace,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> None:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    _require_group_tag(group, GroupTag.WORKSPACE)
    deleted, _ = WorkspaceGroupMembership.objects.filter(
        group=group, workspace=workspace
    ).delete()
    if not deleted:
        raise GroupError(f"Workspace {workspace.name} is not in group {group_name}")


def add_document_to_group(
    group_name: str,
    document: Document,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> None:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    _require_group_tag(group, GroupTag.FILES)
    _enforce_workspace_add(actor=actor, group=group, workspace=document.workspace)
    GroupDocumentMembership.objects.get_or_create(group=group, document=document)


def remove_document_from_group(
    group_name: str,
    document_id: uuid.UUID,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> None:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    _require_group_tag(group, GroupTag.FILES)
    deleted, _ = GroupDocumentMembership.objects.filter(
        group=group, document_id=document_id
    ).delete()
    if not deleted:
        raise GroupError(f"Document {document_id} is not in group {group_name}")


def add_entity_to_group(
    group_name: str,
    entity: KnowledgeEntity,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> None:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    _require_group_tag(group, GroupTag.ENTITY)
    _enforce_workspace_add(actor=actor, group=group, workspace=entity.document.workspace)
    GroupEntityMembership.objects.get_or_create(group=group, entity=entity)


def remove_entity_from_group(
    group_name: str,
    entity_id: uuid.UUID,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> None:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    _require_group_tag(group, GroupTag.ENTITY)
    deleted, _ = GroupEntityMembership.objects.filter(
        group=group, entity_id=entity_id
    ).delete()
    if not deleted:
        raise GroupError(f"Entity {entity_id} is not in group {group_name}")


def add_relation_to_group(
    group_name: str,
    relation: KnowledgeRelation,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> None:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    _require_group_tag(group, GroupTag.RELATION)
    _enforce_workspace_add(
        actor=actor, group=group, workspace=relation.document.workspace
    )
    GroupRelationMembership.objects.get_or_create(group=group, relation=relation)


def remove_relation_from_group(
    group_name: str,
    relation_id: uuid.UUID,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> None:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    _require_group_tag(group, GroupTag.RELATION)
    deleted, _ = GroupRelationMembership.objects.filter(
        group=group, relation_id=relation_id
    ).delete()
    if not deleted:
        raise GroupError(f"Relation {relation_id} is not in group {group_name}")


def delete_group(
    name: str, *, actor: User | None = None, owner_id: int | None = None
) -> None:
    group = get_group_by_name(name, actor=actor, owner_id=owner_id)
    WorkspaceGroupMembership.objects.filter(group=group).delete()
    GroupDocumentMembership.objects.filter(group=group).delete()
    GroupEntityMembership.objects.filter(group=group).delete()
    GroupRelationMembership.objects.filter(group=group).delete()
    group.delete()


def serialize_group_for_api(group: WorkspaceGroup) -> dict:
    return {
        "id": group.pk,
        "name": group.name,
        "owner_id": group.owner_id,
        "owner_username": group.owner.username,
        "tag": group.tag,
        "description": optional_field_for_api(group.description),
        "member_count": get_group_member_count(group),
        "created_at": group.created_at,
    }


def _rename_group_chat_workspace(group: WorkspaceGroup, new_group_name: str) -> None:
    from nodepoint.services.workspace import move_workspace_media_dir

    old_chat = group_chat_workspace_name(group)
    group.name = new_group_name
    new_chat = group_chat_workspace_name(group)
    try:
        chat_workspace = Workspace.objects.get(
            owner_id=group.owner_id, name=old_chat
        )
    except Workspace.DoesNotExist:
        return

    if chat_workspace.name == new_chat:
        return

    if (
        Workspace.objects.filter(owner_id=group.owner_id, name=new_chat)
        .exclude(pk=chat_workspace.pk)
        .exists()
    ):
        raise GroupError(
            f"Cannot rename group: chat workspace '{new_chat}' already exists"
        )

    move_workspace_media_dir(
        chat_workspace, from_name=old_chat, to_name=new_chat
    )
    chat_workspace.name = new_chat
    chat_workspace.save(update_fields=["name"])


def update_group(
    current_name: str,
    updates: dict,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> WorkspaceGroup:
    allowed = {"name", "description"}
    unknown = set(updates) - allowed
    if unknown:
        raise GroupError(f"Unknown fields: {', '.join(sorted(unknown))}")
    if not updates:
        raise GroupError("No fields to update")

    group = get_group_by_name(current_name, actor=actor, owner_id=owner_id)
    old_name = group.name
    update_fields: list[str] = []

    if "name" in updates:
        new_name = validate_group_name(updates["name"])
        if (
            new_name != old_name
            and WorkspaceGroup.objects.filter(owner=group.owner, name=new_name).exists()
        ):
            raise GroupError(f"Group already exists for this owner: {new_name}")
        if new_name != old_name:
            update_fields.append("name")

    if "description" in updates:
        group.description = normalize_description(updates["description"])
        update_fields.append("description")

    rename_to = updates.get("name")
    if rename_to:
        rename_to = validate_group_name(rename_to)

    with transaction.atomic():
        if rename_to and rename_to != old_name:
            _rename_group_chat_workspace(group, rename_to)
            group.name = rename_to
        group.save(update_fields=update_fields)

    return group


def get_group_workspaces_qs(
    group_name: str,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> QuerySet[Workspace]:
    from nodepoint.services.group_scope import resolve_group_search_scope

    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    if group.tag == GroupTag.WORKSPACE:
        return (
            Workspace.objects.filter(group_memberships__group=group)
            .exclude(name__startswith=GROUP_CHAT_PREFIX)
            .exclude(name=LEGACY_FLAGGED_CHAT_WORKSPACE_NAME)
            .order_by("created_at")
            .distinct()
        )
    scope = resolve_group_search_scope(
        group_name, actor=actor, owner_id=owner_id, group=group
    )
    if not scope.workspace_names:
        return Workspace.objects.none()
    return (
        user_workspaces_qs(actor)
        .filter(name__in=scope.workspace_names)
        .order_by("created_at")
    )


def list_group_workspace_names(
    group_name: str,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> list[str]:
    from nodepoint.services.group_scope import resolve_group_search_scope

    return resolve_group_search_scope(
        group_name, actor=actor, owner_id=owner_id
    ).workspace_names


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


def _apply_search_filter(qs, search: str | None, *field_names: str):
    term = (search or "").strip()
    if not term:
        return qs
    q = Q()
    for field in field_names:
        q |= Q(**{f"{field}__icontains": term})
    return qs.filter(q)


def _serialize_workspace_add_option(ws: Workspace) -> dict:
    return {
        "id": ws.pk,
        "name": ws.name,
        "owner_id": ws.owner_id,
        "owner_username": ws.owner.username,
        "tag": optional_field_for_api(ws.tag),
        "description": optional_field_for_api(ws.description),
    }


def _serialize_document_add_option(doc: Document) -> dict:
    ws = doc.workspace
    return {
        "document_id": str(doc.id),
        "workspace": ws.name,
        "workspace_owner_id": ws.owner_id,
        "owner_id": ws.owner_id,
        "owner_username": ws.owner.username,
        "file_name": doc.file_name,
    }


def _serialize_entity_add_option(entity: KnowledgeEntity) -> dict:
    ws = entity.document.workspace
    return {
        "entity_id": str(entity.id),
        "name": entity.name,
        "entity_type": entity.entity_type,
        "workspace": ws.name,
        "document_id": str(entity.document_id),
        "owner_id": ws.owner_id,
        "owner_username": ws.owner.username,
    }


def _serialize_relation_add_option(relation: KnowledgeRelation) -> dict:
    ws = relation.document.workspace
    return {
        "relation_id": str(relation.id),
        "source_name": relation.source.name,
        "target_name": relation.target.name,
        "workspace": ws.name,
        "owner_id": ws.owner_id,
        "owner_username": ws.owner.username,
    }


def list_group_add_options(
    group: WorkspaceGroup,
    *,
    actor: User,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    candidate_owner_id: int | None = None,
) -> dict:
    from nodepoint.services.workspace_catalog import paginate_queryset

    owner_ids = eligible_resource_owner_ids(actor, group)
    ws_base = user_workspaces_qs(actor).select_related("owner")
    if owner_ids is not None:
        ws_base = ws_base.filter(owner_id__in=owner_ids)
    if candidate_owner_id is not None:
        ws_base = ws_base.filter(owner_id=candidate_owner_id)

    if group.tag == GroupTag.WORKSPACE:
        member_ids = WorkspaceGroupMembership.objects.filter(group=group).values_list(
            "workspace_id", flat=True
        )
        qs = ws_base.exclude(pk__in=member_ids).order_by("owner__username", "name")
        qs = _apply_search_filter(qs, search, "name", "tag", "description")
        page_rows, pagination = paginate_queryset(qs, page=page, page_size=page_size)
        items = [_serialize_workspace_add_option(ws) for ws in page_rows]
    elif group.tag == GroupTag.FILES:
        member_ids = GroupDocumentMembership.objects.filter(group=group).values_list(
            "document_id", flat=True
        )
        qs = (
            Document.objects.filter(workspace__in=ws_base)
            .exclude(id__in=member_ids)
            .select_related("workspace", "workspace__owner")
            .order_by("workspace__owner__username", "workspace__name", "file_name")
        )
        qs = _apply_search_filter(qs, search, "file_name", "workspace__name")
        page_rows, pagination = paginate_queryset(qs, page=page, page_size=page_size)
        items = [_serialize_document_add_option(doc) for doc in page_rows]
    elif group.tag == GroupTag.ENTITY:
        member_ids = GroupEntityMembership.objects.filter(group=group).values_list(
            "entity_id", flat=True
        )
        qs = (
            KnowledgeEntity.objects.filter(document__workspace__in=ws_base)
            .exclude(id__in=member_ids)
            .select_related("document__workspace", "document__workspace__owner")
            .order_by("document__workspace__owner__username", "name")
        )
        qs = _apply_search_filter(
            qs, search, "name", "entity_type", "document__workspace__name"
        )
        page_rows, pagination = paginate_queryset(qs, page=page, page_size=page_size)
        items = [_serialize_entity_add_option(entity) for entity in page_rows]
    else:
        member_ids = GroupRelationMembership.objects.filter(group=group).values_list(
            "relation_id", flat=True
        )
        qs = (
            KnowledgeRelation.objects.filter(document__workspace__in=ws_base)
            .exclude(id__in=member_ids)
            .select_related(
                "source",
                "target",
                "document__workspace",
                "document__workspace__owner",
            )
            .order_by("document__workspace__owner__username", "source__name")
        )
        qs = _apply_search_filter(
            qs,
            search,
            "source__name",
            "target__name",
            "document__workspace__name",
        )
        page_rows, pagination = paginate_queryset(qs, page=page, page_size=page_size)
        items = [_serialize_relation_add_option(rel) for rel in page_rows]

    return {
        "group": group.name,
        "tag": group.tag,
        "owner_id": group.owner_id,
        "owner_username": group.owner.username,
        "items": items,
        "pagination": pagination,
    }


def list_workspace_group_options(
    workspace: Workspace,
    *,
    actor: User,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> dict:
    from nodepoint.auth.users import user_role
    from nodepoint.enums import UserRole
    from nodepoint.services.workspace_catalog import paginate_queryset

    base = visible_groups_qs(actor).filter(tag=GroupTag.WORKSPACE).select_related(
        "owner"
    )
    role = user_role(actor)
    if role in (UserRole.ADMIN, UserRole.SUPERADMIN):
        base = base.filter(Q(owner_id=workspace.owner_id) | Q(owner_id=actor.pk))
    else:
        base = base.filter(owner_id=workspace.owner_id)

    base = _apply_search_filter(base, search, "name", "description")
    base = base.order_by("owner__username", "name")

    member_group_ids = set(
        WorkspaceGroupMembership.objects.filter(workspace=workspace).values_list(
            "group_id", flat=True
        )
    )

    eligible_ids = [
        g.pk
        for g in base
        if may_add_to_group(
            actor=actor, group=g, resource_owner_id=workspace.owner_id
        )
        or g.pk in member_group_ids
    ]
    qs = base.filter(pk__in=eligible_ids).order_by("owner__username", "name")
    page_rows, pagination = paginate_queryset(qs, page=page, page_size=page_size)
    groups = [
        {
            "id": g.pk,
            "name": g.name,
            "owner_id": g.owner_id,
            "owner_username": g.owner.username,
            "description": optional_field_for_api(g.description),
            "already_member": g.pk in member_group_ids,
        }
        for g in page_rows
    ]
    return {
        "workspace": workspace.name,
        "owner_id": workspace.owner_id,
        "owner_username": workspace.owner.username,
        "groups": groups,
        "pagination": pagination,
    }


def get_or_create_group_chat_workspace(
    group_name: str,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> Workspace:
    group = get_group_by_name(group_name, actor=actor, owner_id=owner_id)
    chat_name = group_chat_workspace_name(group)
    workspace, _created = Workspace.objects.get_or_create(
        owner=group.owner,
        name=chat_name,
        defaults={},
    )
    from nodepoint.services.workspace import workspace_storage_abspath

    os.makedirs(workspace_storage_abspath(workspace), exist_ok=True)
    return workspace


def get_default_upload_workspace(actor: User) -> Workspace | None:
    """First visible user workspace by created_at (for uploads without workspace_name)."""
    return visible_workspaces_qs(actor).order_by("created_at").first()


def user_workspaces_qs(actor: User | None = None) -> QuerySet[Workspace]:
    """Non-internal workspaces visible to actor (or all if actor is None — legacy)."""
    base = (
        Workspace.objects.exclude(name__startswith=GROUP_CHAT_PREFIX)
        .exclude(name=LEGACY_FLAGGED_CHAT_WORKSPACE_NAME)
    )
    if actor is None:
        return base
    return visible_workspaces_qs(actor)
