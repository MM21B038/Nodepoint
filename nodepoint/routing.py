from typing import Any, cast

from django.urls import path

from nodepoint.consumers.chat import ChatConsumer

websocket_urlpatterns = [
    path(
        "ws/chat/group/<str:group_name>/",
        cast(Any, ChatConsumer.as_asgi()),
    ),
    path("ws/chat/<str:workspace_name>/", cast(Any, ChatConsumer.as_asgi())),
]
