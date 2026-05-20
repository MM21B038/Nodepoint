import os
import uuid
from django.db import models
from .enums import Status


# =========================
# Workspace
# =========================

class Workspace(models.Model):
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


# =========================
# Workspace group
# =========================

class WorkspaceGroup(models.Model):
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

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


# =========================
# Document
# =========================
def workspace_upload_path(instance, filename):

    return os.path.join(
        "workspaces",
        instance.workspace.name,
        filename
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
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"],
                name="unique_conversation_per_workspace",
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