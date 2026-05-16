import logging

from mongoengine import connect

from nodepoint.mongo.models import Content
from nodepoint.settings_loader import mongo_config, mongo_uri

logger = logging.getLogger(__name__)

_cfg = mongo_config()
connect(db=_cfg["database"], host=mongo_uri())


def ingest_document(doc_id, content):
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
