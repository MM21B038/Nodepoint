from __future__ import annotations

import time

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

from nodepoint.auth.api_scopes import scope_for_request
from nodepoint.models import ApiUsageLog

_SKIP_PATH_PREFIXES = (
    "/api/auth/register/",
    "/api/auth/token",
    "/api/auth/account/recover/",
)


class ApiUsageLoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request._api_usage_start = time.perf_counter()

    def process_response(self, request, response):
        path = getattr(request, "path", "") or ""
        if not path.startswith("/api/"):
            return response
        if any(path.startswith(p) for p in _SKIP_PATH_PREFIXES):
            return response
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return response
        start = getattr(request, "_api_usage_start", None)
        duration_ms = 0
        if start is not None:
            duration_ms = int((time.perf_counter() - start) * 1000)
        match = getattr(request, "resolver_match", None)
        url_name = getattr(match, "url_name", "") or "" if match else ""
        required_scope = scope_for_request(url_name, request.method) or ""
        api_key = getattr(request, "auth_api_key", None)
        try:
            ApiUsageLog.objects.create(
                user=user,
                api_key=api_key,
                method=request.method,
                path=path[:500],
                url_name=url_name,
                scope=required_scope,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        except Exception:
            pass
        return response
