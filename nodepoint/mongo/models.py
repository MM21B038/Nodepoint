from mongoengine import Document, StringField


class Content(Document):
    id = StringField(primary_key=True)
    content = StringField(required=True)

    meta = {"collection": "content"}
