from django.db import models


class Status(models.TextChoices):
    PENDING = "PENDING", "Pending"
    QUEUED = "QUEUED", "Queued"
    INPROGRESS = "INPROGRESS", "In Progress"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    TERMINATED = "TERMINATED", "Terminated"
    INVALID = "INVALID", "Invalid"