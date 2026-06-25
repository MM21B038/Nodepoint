from django.contrib import admin

from nodepoint.models import ApiKey, ApiUsageLog, UserProfile, Workspace, WorkspaceGroup


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "status", "managed_by", "purge_scheduled_at")
    list_filter = ("role", "status")
    search_fields = ("user__username",)


@admin.register(ApiUsageLog)
class ApiUsageLogAdmin(admin.ModelAdmin):
    list_display = ("user", "method", "path", "url_name", "status_code", "created_at")
    list_filter = ("method", "status_code")
    readonly_fields = ("created_at",)


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("prefix", "user", "name", "expires_at", "is_active", "created_at")
    readonly_fields = ("key_hash", "prefix", "created_at", "last_used_at")

@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "tag", "created_at")
    list_filter = ("owner", "tag")

@admin.register(WorkspaceGroup)
class WorkspaceGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "tag", "created_at")
    list_filter = ("owner", "tag")