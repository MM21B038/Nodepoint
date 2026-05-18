import logging

from mongoengine import connect

from nodepoint.mongo.models import ChunkContent, Content
from nodepoint.settings_loader import mongo_config, mongo_uri

logger = logging.getLogger(__name__)

_cfg = mongo_config()
connect(db=_cfg["database"], host=mongo_uri())


def ingest_document(doc_id, content):
    """Legacy full-document storage (deprecated for new uploads)."""
    try:
        existing = Content.objects(id=str(doc_id)).first()
        if existing:
            existing.content = content
            existing.save()
        else:
            Content(id=str(doc_id), content=content).save()
        return True
    except Exception:
        logger.exception("Failed to ingest document %s into MongoDB", doc_id)
        return False


def ingest_chunk(chunk_id, document_id, index, content) -> bool:
    try:
        cid = str(chunk_id)
        existing = ChunkContent.objects(id=cid).first()
        if existing:
            existing.document_id = str(document_id)
            existing.index = index
            existing.content = content
            existing.save()
        else:
            ChunkContent(
                id=cid,
                document_id=str(document_id),
                index=index,
                content=content,
            ).save()
        return True
    except Exception:
        logger.exception("Failed to ingest chunk %s into MongoDB", chunk_id)
        return False


def get_document_text(document_id) -> str | None:
    """Full document text: legacy Mongo Content, else joined chunks, else file on disk."""
    try:
        row = Content.objects(id=str(document_id)).first()
        if row is not None and row.content:
            return row.content
    except Exception:
        logger.exception("Failed to read legacy Mongo content for document %s", document_id)

    try:
        from nodepoint.models import Document, DocumentChunk

        chunks = DocumentChunk.objects.filter(document_id=document_id).order_by("index")
        parts: list[str] = []
        for chunk in chunks:
            text = get_chunk_text(chunk.id)
            if text:
                parts.append(text)
        if parts:
            return "\n\n".join(parts)

        document = Document.objects.get(id=document_id)
        if document.file:
            from nodepoint.backend.content_extractor import read_document_content
            import os

            path = document.file.path
            if os.path.exists(path):
                return read_document_content(path)
    except Exception:
        logger.exception("Failed to assemble document text for %s", document_id)
    return None


def get_chunk_text(chunk_id) -> str | None:
    try:
        row = ChunkContent.objects(id=str(chunk_id)).first()
        if row is None:
            return None
        return row.content
    except Exception:
        logger.exception("Failed to read chunk %s from MongoDB", chunk_id)
        return None


def delete_chunks_for_document(document_id) -> None:
    try:
        ChunkContent.objects(document_id=str(document_id)).delete()
    except Exception:
        logger.exception("Failed to delete Mongo chunks for document %s", document_id)
