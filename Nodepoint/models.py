import os
import uuid
from django.db import models


# =========================
# Workspace
# =========================

class Workspace(models.Model):
    name = models.CharField(max_length=255, unique=True)
    is_flag = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


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
    id = models.BigAutoField(primary_key=True)

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="documents"
    )

    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False
    )

    file_name = models.CharField(max_length=500)

    file = models.FileField(
        upload_to=workspace_upload_path
    )

    extracted_text = models.TextField(
        blank=True,
        null=True
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


# =========================
# Knowledge Entity
# =========================

class KnowledgeEntity(models.Model):

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="entities"
    )

    name = models.CharField(max_length=255)

    entity_type = models.CharField(max_length=100)

    attributes = models.JSONField(
        blank=True,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.entity_type})"


# =========================
# Knowledge Relation
# =========================

class KnowledgeRelation(models.Model):

    uuid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="relations"
    )

    relationship = models.JSONField()

    description = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.description[:50]


# # =========================
# # Embedding
# # =========================

# class VectorEmbedding(models.Model):

#     ENTITY = "entity"
#     RELATION = "relation"

#     EMBEDDING_TYPES = [
#         (ENTITY, "Entity"),
#         (RELATION, "Relation"),
#     ]

#     id = models.BigAutoField(primary_key=True)

#     embedding_type = models.CharField(
#         max_length=20,
#         choices=EMBEDDING_TYPES
#     )

#     reference_uuid = models.UUIDField()

#     document = models.ForeignKey(
#         Document,
#         on_delete=models.CASCADE,
#         related_name="embeddings"
#     )

#     embedding = VectorField(
#         dimensions=768,
#         null=True,
#         blank=True
#     )

#     created_at = models.DateTimeField(auto_now_add=True)

#     class Meta:
#         indexes = [
#             models.Index(fields=["reference_uuid"]),
#             models.Index(fields=["embedding_type"]),
#         ]

#     def __str__(self):
#         return f"{self.embedding_type} embedding"


# =========================
# Conversation Memory
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