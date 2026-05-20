from django.urls import path

from nodepoint.consumers.chat import ChatConsumer

websocket_urlpatterns = [
    path(
        "ws/chat/group/<str:group_name>/",
        ChatConsumer.as_asgi(),
    ),
    path("ws/chat/<str:workspace_name>/", ChatConsumer.as_asgi()),
]
