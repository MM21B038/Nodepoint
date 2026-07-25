import sys

from django.apps import AppConfig


class NodepointConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nodepoint"

    def ready(self) -> None:
        from nodepoint.services.chat_turn_cancel_listener import (
            should_start_chat_cancel_listener,
            start_chat_turn_cancel_listener,
        )

        if should_start_chat_cancel_listener():
            start_chat_turn_cancel_listener()
