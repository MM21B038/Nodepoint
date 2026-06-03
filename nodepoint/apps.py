import sys

from django.apps import AppConfig


class NodepointConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nodepoint"

    def ready(self) -> None:
        if {"rqworker", "rqworker-pool"}.intersection(sys.argv):
            import django_rq.utils

            from nodepoint.mongo.manager import reset_mongo_connection

            reset_db_connections = django_rq.utils.reset_db_connections

            def reset_connections() -> None:
                reset_db_connections()
                reset_mongo_connection()

            django_rq.utils.reset_db_connections = reset_connections

        from nodepoint.services.chat_turn_cancel_listener import (
            should_start_chat_cancel_listener,
            start_chat_turn_cancel_listener,
        )

        if should_start_chat_cancel_listener():
            start_chat_turn_cancel_listener()
