from django.urls import path

from nodepoint.consumers.chat import ChatConsumer

websocket_urlpatterns = [
    path(
        "ws/chat/flagged/",
        ChatConsumer.as_asgi(),
        {"flagged_scope": True},
    ),
    path("ws/chat/<str:workspace_name>/", ChatConsumer.as_asgi()),
]
