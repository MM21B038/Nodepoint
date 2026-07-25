from __future__ import annotations

from typing import Any

from mongoengine import Document, IntField, StringField
from mongoengine.queryset.manager import QuerySetManager


class DocumentQuerySetManager(QuerySetManager):
    """Typed QuerySetManager so ``Model.objects(...)`` type-checks.

    Runtime access still goes through the descriptor (``__get__`` → QuerySet).
    Returning ``Any`` from ``__get__`` matches mongoengine's untyped queryset API.
    """

    def __get__(self, instance: Any, owner: Any = None) -> Any:
        return super().__get__(instance, owner)


class Content(Document):
    objects = DocumentQuerySetManager()

    id = StringField(primary_key=True)
    content = StringField(required=True)

    meta = {"collection": "content"}


class ChunkContent(Document):
    objects = DocumentQuerySetManager()

    id = StringField(primary_key=True)
    document_id = StringField(required=True)
    index = IntField(required=True)
    content = StringField(required=True)

    meta = {
        "collection": "chunk_content",
        "indexes": ["document_id"],
    }
