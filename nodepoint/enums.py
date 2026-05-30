from django.db import models


class Status(models.TextChoices):
    PENDING = "PENDING", "Pending"
    QUEUED = "QUEUED", "Queued"
    INPROGRESS = "INPROGRESS", "In Progress"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    TERMINATED = "TERMINATED", "Terminated"
    INVALID = "INVALID", "Invalid"

class ChatMessageRole(models.TextChoices):
    SYSTEM = "system", "System"
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    TOOL = "tool", "Tool"


class GroupTag(models.TextChoices):
    WORKSPACE = "workspace", "Workspace"
    FILES = "files", "Files"
    ENTITY = "entity", "Entity"
    RELATION = "relation", "Relation"