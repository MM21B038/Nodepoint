from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.password_validation import validate_password
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from nodepoint.auth.account_lifecycle import (
    AccountLifecycleError,
    deletion_status_payload,
    recover_account,
    schedule_user_deletion,
)
from nodepoint.auth.user_lifecycle import (
    activate_user,
    deactivate_user,
    filter_api_keys,
    filter_manageable_users,
    purge_user_permanently,
    user_list_annotations,
)
from nodepoint.auth.api_keys import create_api_key
from nodepoint.auth.permissions import AllowAnyPermission, IsAdminOrAbove, IsSuperAdmin
from nodepoint.auth.scopes import effective_allowed_scopes
from nodepoint.auth.serializers import (
    AccountDeleteSerializer,
    AccountRecoverSerializer,
    AdminCreateUserSerializer,
    AllowedScopesSerializer,
    ChangePasswordSerializer,
    ApiKeyCreateSerializer,
    ApiKeySerializer,
    ApiKeyUpdateSerializer,
    RegisterSerializer,
    ScopesListSerializer,
    UserSerializer,
    UserUpdateSerializer,
)
from nodepoint.auth.token_serializers import NodepointTokenObtainPairSerializer
from nodepoint.auth.usage import (
    assert_can_view_usage,
    usage_platform_summary,
    usage_summary_for_user,
)
from nodepoint.auth.users import create_account, ensure_profile, user_role
from nodepoint.auth.visibility import can_manage_user, manageable_users_qs
from nodepoint.enums import AccountStatus, UserRole
from nodepoint.models import ApiKey

User = get_user_model()


class PublicAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAnyPermission]


class RegisterAPIView(PublicAPIView):
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class TokenObtainAPIView(TokenObtainPairView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = NodepointTokenObtainPairSerializer


class TokenRefreshAPIView(TokenRefreshView):
    authentication_classes = []
    permission_classes = [AllowAny]


class MeAPIView(APIView):
    def get(self, request):
        user = User.objects.select_related("profile").get(pk=request.user.pk)
        return Response(UserSerializer(user).data)


class MeChangePasswordAPIView(APIView):
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not check_password(data["current_password"], request.user.password):
            return Response(
                {"error": "Invalid current password"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_password(data["new_password"], user=request.user)
        except DjangoValidationError as exc:
            return Response(
                {"password": list(exc.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        request.user.set_password(data["new_password"])
        request.user.save(update_fields=["password"])
        return Response({"message": "Password updated"})


class MeAllowedScopesAPIView(APIView):
    def get(self, request):
        scopes = effective_allowed_scopes(request.user)
        return Response(
            {
                "allowed_scopes": scopes,
                "unrestricted": scopes is None,
            }
        )


class AccountDeleteAPIView(APIView):
    def post(self, request):
        serializer = AccountDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not check_password(serializer.validated_data["confirm_password"], request.user.password):
            return Response(
                {"error": "Invalid password"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            profile = schedule_user_deletion(request.user, requested_by=request.user)
        except AccountLifecycleError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(deletion_status_payload(request.user) | {"message": "Account scheduled for deletion"})


class AccountRecoverAPIView(PublicAPIView):
    def post(self, request):
        serializer = AccountRecoverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            user = recover_account(
                username=data["username"],
                password=data["password"],
                role=data["role"],
            )
        except AccountLifecycleError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"message": "Account recovered", "username": user.username},
            status=status.HTTP_200_OK,
        )


class AccountDeletionStatusAPIView(APIView):
    def get(self, request):
        return Response(deletion_status_payload(request.user))


class UserListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrAbove]

    def get(self, request):
        from nodepoint.services.workspace_catalog import paginate_queryset, parse_pagination

        params = request.query_params
        try:
            page, page_size = parse_pagination(
                params.get("page"), params.get("page_size")
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        is_active_raw = (params.get("is_active") or "").strip().lower()
        is_active = None
        if is_active_raw in ("1", "true", "yes"):
            is_active = True
        elif is_active_raw in ("0", "false", "no"):
            is_active = False

        qs = manageable_users_qs(request.user).select_related(
            "profile", "profile__managed_by"
        )
        try:
            qs = filter_manageable_users(
                qs,
                role=(params.get("role") or "").strip() or None,
                account_state=(params.get("account_state") or "").strip() or None,
                status=(params.get("status") or "").strip() or None,
                is_active=is_active,
                search=(params.get("search") or "").strip() or None,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        qs = user_list_annotations(qs).order_by("username")
        users, pagination = paginate_queryset(qs, page=page, page_size=page_size)
        return Response(
            {
                "users": UserSerializer(users, many=True).data,
                "pagination": pagination,
                "filters": {
                    "role": params.get("role"),
                    "account_state": params.get("account_state"),
                    "status": params.get("status"),
                    "is_active": params.get("is_active"),
                    "search": params.get("search"),
                },
            }
        )

    def post(self, request):
        serializer = AdminCreateUserSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        role = data["role"]
        managed_by_id = data.get("managed_by_id")

        if user_role(request.user) == UserRole.ADMIN:
            role = UserRole.USER
            managed_by = request.user
        elif role == UserRole.USER:
            managed_by = (
                User.objects.get(pk=managed_by_id)
                if managed_by_id is not None
                else request.user
            )
        else:
            managed_by = None

        user = create_account(
            username=data["username"],
            password=data["password"],
            role=role,
            managed_by=managed_by,
            created_by=request.user,
            allowed_scopes=data.get("allowed_scopes"),
        )
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class UserDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrAbove]

    def _get_target(self, request, user_id: int):
        try:
            target = User.objects.select_related("profile", "profile__managed_by").get(
                pk=user_id
            )
        except User.DoesNotExist:
            return None
        if not can_manage_user(request.user, target):
            return False
        return target

    def get(self, request, user_id: int):
        target = self._get_target(request, user_id)
        if target is None:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if target is False:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        return Response(UserSerializer(target).data)

    def patch(self, request, user_id: int):
        target = self._get_target(request, user_id)
        if target is None:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if target is False:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        serializer = UserUpdateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "password" in data:
            target.set_password(data["password"])
        profile = ensure_profile(target)
        if "is_active" in data:
            try:
                if data["is_active"]:
                    activate_user(target, requested_by=request.user)
                else:
                    deactivate_user(target, requested_by=request.user)
            except AccountLifecycleError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if "allowed_scopes" in data:
            profile.allowed_scopes = data["allowed_scopes"]
            profile.save(update_fields=["allowed_scopes"])
        target.save()
        return Response(UserSerializer(target).data)

    def delete(self, request, user_id: int):
        if request.user.pk == user_id:
            return Response(
                {"error": "Use POST /api/auth/account/delete/ for self-deletion"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        target = self._get_target(request, user_id)
        if target is None:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if target is False:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        try:
            schedule_user_deletion(target, requested_by=request.user)
        except AccountLifecycleError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(deletion_status_payload(target), status=status.HTTP_200_OK)


class UserPurgeAPIView(APIView):
    """Permanent delete: user row, owned data, workspace media. Not recoverable."""

    permission_classes = [IsAuthenticated, IsAdminOrAbove]

    def post(self, request, user_id: int):
        if request.user.pk == user_id:
            return Response(
                {"error": "Cannot purge your own account"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            target = User.objects.select_related("profile").get(pk=user_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_user(request.user, target):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        try:
            summary = purge_user_permanently(target, requested_by=request.user)
        except AccountLifecycleError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(summary, status=status.HTTP_200_OK)


class UserAllowedScopesAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrAbove]

    def _get_target(self, request, user_id: int):
        try:
            target = User.objects.select_related("profile").get(pk=user_id)
        except User.DoesNotExist:
            return None
        if not can_manage_user(request.user, target):
            return False
        return target

    def get(self, request, user_id: int):
        target = self._get_target(request, user_id)
        if target is None:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if target is False:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        scopes = effective_allowed_scopes(target)
        return Response({"user_id": user_id, "allowed_scopes": scopes, "unrestricted": scopes is None})

    def patch(self, request, user_id: int):
        target = self._get_target(request, user_id)
        if target is None:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if target is False:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        serializer = AllowedScopesSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        profile = ensure_profile(target)
        profile.allowed_scopes = serializer.validated_data.get("allowed_scopes")
        profile.save(update_fields=["allowed_scopes"])
        scopes = effective_allowed_scopes(target)
        return Response({"user_id": user_id, "allowed_scopes": scopes, "unrestricted": scopes is None})


class ApiKeyListCreateAPIView(APIView):
    def get(self, request):
        from nodepoint.services.workspace_catalog import paginate_queryset, parse_pagination

        params = request.query_params
        try:
            page, page_size = parse_pagination(
                params.get("page"), params.get("page_size")
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if user_role(request.user) in (UserRole.SUPERADMIN, UserRole.ADMIN):
            user_ids = list(manageable_users_qs(request.user).values_list("pk", flat=True))
            qs = ApiKey.objects.filter(user_id__in=user_ids).select_related(
                "user", "user__profile"
            )
        else:
            qs = ApiKey.objects.filter(user=request.user).select_related(
                "user", "user__profile"
            )

        is_active_raw = (params.get("is_active") or "").strip().lower()
        is_active = None
        if is_active_raw in ("1", "true", "yes"):
            is_active = True
        elif is_active_raw in ("0", "false", "no"):
            is_active = False

        user_id_raw = (params.get("user_id") or "").strip()
        user_id = int(user_id_raw) if user_id_raw else None

        include_expired = (params.get("include_expired") or "true").strip().lower() not in (
            "0",
            "false",
            "no",
        )

        qs = filter_api_keys(
            qs,
            is_active=is_active,
            user_id=user_id,
            role=(params.get("role") or "").strip() or None,
            include_expired=include_expired,
        )
        keys, pagination = paginate_queryset(
            qs.order_by("-created_at"), page=page, page_size=page_size
        )
        return Response(
            {
                "api_keys": ApiKeySerializer(keys, many=True).data,
                "pagination": pagination,
                "filters": {
                    "user_id": params.get("user_id"),
                    "role": params.get("role"),
                    "is_active": params.get("is_active"),
                    "include_expired": params.get("include_expired"),
                },
            }
        )

    def post(self, request):
        profile = ensure_profile(request.user)
        if profile.status == AccountStatus.PENDING_DELETION:
            return Response(
                {"error": "Cannot create API keys while account is pending deletion"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ApiKeyCreateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            target_user = User.objects.get(pk=data["user_id"])
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_user(request.user, target_user):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        if user_role(request.user) == UserRole.USER and target_user.pk != request.user.pk:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        target_profile = ensure_profile(target_user)
        if target_profile.status == AccountStatus.PENDING_DELETION:
            return Response(
                {"error": "Target account is pending deletion"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not target_user.is_active:
            return Response(
                {"error": "Cannot create API keys for an inactive user account"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        api_key, full_key = create_api_key(
            user=target_user,
            created_by=request.user,
            name=data.get("name", ""),
            scopes=data["scopes"],
            expiry_preset=data["expiry_preset"],
        )
        payload = ApiKeySerializer(api_key).data
        payload["key"] = full_key
        return Response(payload, status=status.HTTP_201_CREATED)


class ApiKeyDetailAPIView(APIView):
    def patch(self, request, key_id):
        try:
            api_key = ApiKey.objects.select_related("user", "user__profile").get(pk=key_id)
        except ApiKey.DoesNotExist:
            return Response({"error": "API key not found"}, status=status.HTTP_404_NOT_FOUND)
        if api_key.user_id != request.user.pk and not can_manage_user(
            request.user, api_key.user
        ):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        serializer = ApiKeyUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "is_active" in data:
            if data["is_active"] and not api_key.user.is_active:
                return Response(
                    {"error": "Cannot activate API key for an inactive user account"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            api_key.is_active = data["is_active"]
        if "name" in data:
            api_key.name = data["name"]
        api_key.save(update_fields=[f for f in ("is_active", "name") if f in data])
        return Response(ApiKeySerializer(api_key).data)

    def delete(self, request, key_id):
        try:
            api_key = ApiKey.objects.select_related("user").get(pk=key_id)
        except ApiKey.DoesNotExist:
            return Response({"error": "API key not found"}, status=status.HTTP_404_NOT_FOUND)
        if api_key.user_id != request.user.pk and not can_manage_user(
            request.user, api_key.user
        ):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        if api_key.is_active:
            return Response(
                {
                    "error": "Deactivate the key first (PATCH is_active=false), then DELETE to remove from database"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        api_key.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ScopesListAPIView(APIView):
    def get(self, request):
        return Response(ScopesListSerializer({}).data)


class UsageMeAPIView(APIView):
    def get(self, request):
        days = int(request.query_params.get("days", 30))
        return Response(usage_summary_for_user(request.user, days=days))


class UsagePlatformAPIView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        days = int(request.query_params.get("days", 30))
        return Response(usage_platform_summary(days=days))


class UsageUserAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrAbove]

    def get(self, request, user_id: int):
        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            assert_can_view_usage(request.user, target)
        except PermissionError:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        days = int(request.query_params.get("days", 30))
        return Response(usage_summary_for_user(target, days=days))
