from __future__ import annotations

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from nodepoint.auth.api_keys import EXPIRY_PRESETS
from nodepoint.auth.api_scopes import SCOPE_CODES, list_scopes_for_api
from nodepoint.auth.scopes import effective_allowed_scopes, validate_scopes_subset
from nodepoint.auth.users import User, create_account, ensure_profile, user_role
from nodepoint.enums import AccountStatus, UserRole
from nodepoint.models import ApiKey
from typing import Any, List, Mapping


def _normalize_allowed_scopes_payload(data: object) -> object:
    """Map UI alias `access` -> `allowed_scopes` for create/edit scope bodies."""
    if not isinstance(data, dict):
        return data
    if "allowed_scopes" not in data and "access" in data:
        data = dict(data)
        data["allowed_scopes"] = data.pop("access")
    return data


def _validate_allowed_scopes_for_assigner(
    value, *, context: Mapping[str, Any]
) -> List[str] | None:
    if value is None:
        return value
    request = context.get("request")
    if request is None:
        return value
    try:
        return validate_scopes_subset(value, assigner=request.user)
    except ValueError as exc:
        raise serializers.ValidationError(str(exc)) from exc


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(choices=[UserRole.USER, UserRole.ADMIN])

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Username already taken")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_role(self, value):
        if value == UserRole.ADMIN and not settings.ALLOW_OPEN_ADMIN_SIGNUP:
            raise serializers.ValidationError("Admin self-registration is disabled")
        if value == UserRole.SUPERADMIN:
            raise serializers.ValidationError("Cannot register as superadmin")
        return value

    def create(self, validated_data):
        return create_account(
            username=validated_data["username"],
            password=validated_data["password"],
            role=validated_data["role"],
        )


class UserSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    status = serializers.CharField(source="profile.status", read_only=True)
    account_state = serializers.SerializerMethodField()
    purge_scheduled_at = serializers.DateTimeField(
        source="profile.purge_scheduled_at", read_only=True, allow_null=True
    )
    allowed_scopes = serializers.JSONField(
        source="profile.allowed_scopes", read_only=True, allow_null=True
    )
    managed_by = serializers.IntegerField(
        source="profile.managed_by_id", read_only=True, allow_null=True
    )
    managed_by_username = serializers.CharField(
        source="profile.managed_by.username", read_only=True, allow_null=True
    )
    api_keys_total = serializers.IntegerField(read_only=True, required=False)
    api_keys_active = serializers.IntegerField(read_only=True, required=False)
    workspaces_total = serializers.IntegerField(read_only=True, required=False)
    can_activate = serializers.SerializerMethodField()
    can_deactivate = serializers.SerializerMethodField()
    can_purge_permanently = serializers.SerializerMethodField()
    can_recover = serializers.SerializerMethodField()

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = User
        fields = (
            "id",
            "username",
            "role",
            "status",
            "account_state",
            "is_active",
            "purge_scheduled_at",
            "allowed_scopes",
            "managed_by",
            "managed_by_username",
            "date_joined",
            "api_keys_total",
            "api_keys_active",
            "workspaces_total",
            "can_activate",
            "can_deactivate",
            "can_purge_permanently",
            "can_recover",
        )
        read_only_fields = fields

    def get_role(self, obj):
        return user_role(obj)

    def get_account_state(self, obj):
        from nodepoint.auth.user_lifecycle import account_state_label

        return account_state_label(obj)

    def get_can_activate(self, obj):
        from nodepoint.auth.user_lifecycle import can_activate

        return can_activate(obj)

    def get_can_deactivate(self, obj):
        from nodepoint.auth.user_lifecycle import account_state_label

        return account_state_label(obj) == "active"

    def get_can_purge_permanently(self, obj):
        from nodepoint.auth.user_lifecycle import can_permanently_purge

        return can_permanently_purge(obj)

    def get_can_recover(self, obj):
        from nodepoint.enums import AccountStatus

        profile = getattr(obj, "profile", None)
        return bool(profile and profile.status == AccountStatus.PENDING_DELETION)


class AdminCreateUserSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(
        choices=[UserRole.USER, UserRole.ADMIN],
        default=UserRole.USER,
    )
    managed_by_id = serializers.IntegerField(required=False, allow_null=True)
    allowed_scopes = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Username already taken")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_allowed_scopes(self, value):
        return _validate_allowed_scopes_for_assigner(value, context=self.context)

    def to_internal_value(self, data):
        return super().to_internal_value(_normalize_allowed_scopes_payload(data))


class AllowedScopesSerializer(serializers.Serializer):
    allowed_scopes = serializers.ListField(
        child=serializers.CharField(), allow_null=True, required=False
    )

    def validate_allowed_scopes(self, value):
        return _validate_allowed_scopes_for_assigner(value, context=self.context)

    def to_internal_value(self, data):
        return super().to_internal_value(_normalize_allowed_scopes_payload(data))

    def validate(self, attrs):
        raw = self.initial_data
        if isinstance(raw, dict) and raw:
            normalized = _normalize_allowed_scopes_payload(raw)
            if isinstance(normalized, dict) and "allowed_scopes" not in normalized:
                raise serializers.ValidationError(
                    "Provide allowed_scopes (scope code array). "
                    "The field access is accepted as an alias for allowed_scopes."
                )
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class AccountDeleteSerializer(serializers.Serializer):
    confirm_password = serializers.CharField(write_only=True)


class AccountRecoverSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(
        choices=[UserRole.USER, UserRole.ADMIN, UserRole.SUPERADMIN]
    )


class UserUpdateSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, required=False)
    is_active = serializers.BooleanField(required=False)
    allowed_scopes = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )

    def validate_password(self, value):
        if value:
            validate_password(value)
        return value

    def validate_allowed_scopes(self, value):
        return _validate_allowed_scopes_for_assigner(value, context=self.context)

    def to_internal_value(self, data):
        return super().to_internal_value(_normalize_allowed_scopes_payload(data))


class ApiKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    user_id = serializers.IntegerField()
    scopes = serializers.ListField(child=serializers.CharField())
    expiry_preset = serializers.ChoiceField(choices=list(EXPIRY_PRESETS.keys()))

    def validate_scopes(self, value):
        unknown = [s for s in value if s not in SCOPE_CODES]
        if unknown:
            raise serializers.ValidationError(f"Unknown scopes: {unknown}")
        if not value:
            raise serializers.ValidationError("At least one scope is required")
        return _validate_allowed_scopes_for_assigner(value, context=self.context)


class ApiKeySerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source="user.username", read_only=True)
    user_role = serializers.SerializerMethodField()
    key_status = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = ApiKey
        fields = (
            "id",
            "name",
            "user_id",
            "user_username",
            "user_role",
            "allowed_scopes",
            "expires_at",
            "is_active",
            "key_status",
            "is_expired",
            "last_used_at",
            "created_at",
        )
        read_only_fields = fields

    def get_user_role(self, obj):
        return user_role(obj.user)

    def get_key_status(self, obj):
        from django.utils import timezone

        if not obj.is_active:
            return "inactive"
        if obj.expires_at <= timezone.now():
            return "expired"
        return "active"

    def get_is_expired(self, obj):
        from django.utils import timezone

        return obj.expires_at <= timezone.now()


class ApiKeyUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide is_active and/or name.")
        return attrs


class ScopesListSerializer(serializers.Serializer):
    scopes = serializers.SerializerMethodField()
    expiry_presets = serializers.SerializerMethodField()

    def get_scopes(self, obj):
        return list_scopes_for_api()

    def get_expiry_presets(self, obj):
        return list(EXPIRY_PRESETS.keys())
