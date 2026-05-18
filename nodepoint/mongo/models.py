from mongoengine import Document, IntField, StringField


class Content(Document):
    id = StringField(primary_key=True)
    content = StringField(required=True)

    meta = {"collection": "content"}


class ChunkContent(Document):
    id = StringField(primary_key=True)
    document_id = StringField(required=True)
    index = IntField(required=True)
    content = StringField(required=True)

    meta = {
        "collection": "chunk_content",
        "indexes": ["document_id"],
    }
