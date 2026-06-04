import os
import uuid

from django.conf import settings
from django.db import models

from .enums import AccountStatus, GroupTag, Status, UserRole


# =========================
# User profile & API keys
# =========================


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.USER,
    )
    managed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_users",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_accounts",
    )
    status = models.CharField(
        max_length=20,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
    )
    allowed_scopes = models.JSONField(null=True, blank=True)
    deletion_requested_at = models.DateTimeField(null=True, blank=True)
    purge_scheduled_at = models.DateTimeField(null=True, blank=True)
    deletion_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deletions_requested",
    )

    def __str__(self):
        return f"{self.user.username} ({self.role})"

    @property
    def is_superadmin(self) -> bool:
        return self.role == UserRole.SUPERADMIN

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def is_end_user(self) -> bool:
        return self.role == UserRole.USER


class ApiKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="api_keys_created",
    )
    name = models.CharField(max_length=255, blank=True, default="")
    prefix = models.CharField(max_length=16, db_index=True)
    key_hash = models.CharField(max_length=64)
    allowed_scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["prefix", "is_active"]),
        ]

    def __str__(self):
        return f"{self.name or self.prefix} ({self.user.username})"


class ApiUsageLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_usage_logs",
        null=True,
        blank=True,
    )
    api_key = models.ForeignKey(
        ApiKey,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_logs",
    )
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=500)
    url_name = models.CharField(max_length=100, blank=True, default="")
    scope = models.CharField(max_length=64, blank=True, default="")
    status_code = models.PositiveSmallIntegerField()
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"], name="apiusage_user_created"),
            models.Index(fields=["url_name", "-created_at"], name="apiusage_url_created"),
        ]

    def __str__(self):
        return f"{self.method} {self.path} ({self.status_code})"


# =========================
# Workspace
# =========================

class Workspace(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspaces",
    )
    tag = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"],
                name="unique_workspace_name_per_owner",
            )
        ]

    def __str__(self):
        return self.name


# =========================
# Workspace group
# =========================

class WorkspaceGroup(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspace_groups",
    )
    tag = models.CharField(
        max_length=16,
        choices=GroupTag.choices,
        default=GroupTag.WORKSPACE,
    )
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"],
                name="unique_group_name_per_owner",
            )
        ]

    def __str__(self):
        return self.name


class WorkspaceGroupMembership(models.Model):
    group = models.ForeignKey(
        WorkspaceGroup,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["group", "workspace"],
                name="unique_workspace_per_group",
            )
        ]

    def __str__(self):
        return f"{self.workspace.name} in {self.group.name}"


class GroupDocumentMembership(models.Model):
    group = models.ForeignKey(
        WorkspaceGroup,
        on_delete=models.CASCADE,
        related_name="document_memberships",
    )
    document = models.ForeignKey(
        "Document",
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["group", "document"],
                name="unique_document_per_group",
            )
        ]

    def __str__(self):
        return f"{self.document.file_name} in {self.group.name}"


class GroupEntityMembership(models.Model):
    group = models.ForeignKey(
        WorkspaceGroup,
        on_delete=models.CASCADE,
        related_name="entity_memberships",
    )
    entity = models.ForeignKey(
        "KnowledgeEntity",
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["group", "entity"],
                name="unique_entity_per_group",
            )
        ]

    def __str__(self):
        return f"{self.entity.name} in {self.group.name}"


class GroupRelationMembership(models.Model):
    group = models.ForeignKey(
        WorkspaceGroup,
        on_delete=models.CASCADE,
        related_name="relation_memberships",
    )
    relation = models.ForeignKey(
        "KnowledgeRelation",
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["group", "relation"],
                name="unique_relation_per_group",
            )
        ]

    def __str__(self):
        return f"relation {self.relation_id} in {self.group.name}"


# =========================
# Document
# =========================
def workspace_upload_path(instance, filename):
    return os.path.join(
        "workspaces",
        str(instance.workspace.owner_id),
        instance.workspace.name,
        filename,
    )

class Document(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="documents"
    )

    file_name = models.CharField(max_length=500)

    file = models.FileField(
        upload_to=workspace_upload_path
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )

    content = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "file_name"],
                name="unique_file_per_workspace"
            )
        ]

    def __str__(self):
        return self.file_name

    def __repr__(self):
        return str(self.id)


# =========================
# Document Chunk
# =========================

class DocumentChunk(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="chunks",
    )

    index = models.PositiveIntegerField()

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    vector = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "index"],
                name="unique_chunk_index_per_document",
            )
        ]

    def __str__(self):
        return f"{self.document_id}#{self.index}"


# =========================
# Knowledge Entity
# =========================

class KnowledgeEntity(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="entities"
    )

    chunk = models.ForeignKey(
        DocumentChunk,
        on_delete=models.CASCADE,
        related_name="entities",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)

    entity_type = models.CharField(max_length=100, null=True, blank=True)

    attributes = models.JSONField(
        blank=True,
        null=True,
    )

    vector = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )


    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.entity_type})"
    
    def __repr__(self):
        return str(self.id)


# =========================
# Knowledge Relation
# =========================

class KnowledgeRelation(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="relations"
    )

    chunk = models.ForeignKey(
        DocumentChunk,
        on_delete=models.CASCADE,
        related_name="relations",
        null=True,
        blank=True,
    )

    source = models.ForeignKey(
        KnowledgeEntity,
        on_delete=models.CASCADE,
        related_name="source_relations"
    )

    target = models.ForeignKey(
        KnowledgeEntity,
        on_delete=models.CASCADE,
        related_name="target_relations"
    )

    type_description = models.TextField(
        blank=True,
        null=True
    )

    description = models.TextField(
        blank=True,
        null=True
    )

    vector = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.source.name} --[{self.type_description}]--> {self.target.name}"
    
    def __repr__(self):
        return str(self.id)


# =========================
# Chat
# =========================


class ChatMessageRole(models.TextChoices):
    SYSTEM = "system", "System"
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    TOOL = "tool", "Tool"


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    title = models.CharField(max_length=500, blank=True, default="")
    system_prompt = models.TextField(blank=True, default="")
    compressions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["workspace", "-updated_at"],
                name="conversation_workspace_updated",
            ),
        ]

    def __str__(self):
        return self.title or str(self.id)


class ChatBranch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="branches",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    is_root = models.BooleanField(default=False)
    is_internal = models.BooleanField(
        default=False,
        help_text="Compression handoff branch; hidden from user-facing APIs.",
    )
    label = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation"],
                condition=models.Q(is_root=True),
                name="unique_root_branch_per_conversation",
            ),
        ]


class ChatMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        ChatBranch,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sequence = models.PositiveIntegerField()
    role = models.CharField(max_length=20, choices=ChatMessageRole.choices)
    content = models.TextField(blank=True, default="")
    reasoning_content = models.TextField(blank=True, null=True)
    tool_calls = models.JSONField(blank=True, null=True)
    tool_call_id = models.CharField(max_length=255, blank=True, null=True)
    tool_name = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "sequence"],
                name="unique_message_sequence_per_branch",
            ),
        ]


# =========================
# Conversation Memory (legacy sketch)
# =========================

# class ConversationMemory(models.Model):

#     id = models.UUIDField(
#         primary_key=True,
#         default=uuid.uuid4,
#         editable=False
#     )

#     workspace = models.ForeignKey(
#         Workspace,
#         on_delete=models.CASCADE,
#         related_name="memories"
#     )

#     query = models.TextField(
#         blank=True,
#         null=True
#     )

#     response = models.TextField(
#         blank=True,
#         null=True
#     )

#     source = models.JSONField(
#         blank=True,
#         null=True
#     )

#     embedding = VectorField(
#         dimensions=768,
#         null=True,
#         blank=True
#     )

#     engine = models.CharField(
#         max_length=100,
#         blank=True,
#         null=True
#     )

#     filters = models.JSONField(
#         blank=True,
#         null=True
#     )

#     requested_at = models.DateTimeField(auto_now_add=True)

#     responded_at = models.DateTimeField(
#         auto_now=True
#     )

#     def __str__(self):
#         return f"Memory {self.id}"