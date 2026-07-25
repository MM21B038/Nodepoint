from __future__ import annotations

from typing import cast

from django.contrib.auth.hashers import check_password
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from nodepoint.auth.account_lifecycle import login_block_message, profile_allows_login
from nodepoint.auth.users import User, ensure_profile


class NodepointTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        username = attrs.get(self.username_field)
        password = attrs.get("password")
        try:
            user = User.objects.get(**{self.username_field: username})
        except User.DoesNotExist:
            raise AuthenticationFailed(
                "No active account found with the given credentials",
                code="no_active_account",
            )
        if not check_password(password, user.password):
            raise AuthenticationFailed(
                "No active account found with the given credentials",
                code="no_active_account",
            )
        profile = ensure_profile(user)
        if not profile_allows_login(profile):
            msg = login_block_message(profile) or "Account cannot log in."
            raise AuthenticationFailed(msg, code="account_blocked")
        if not user.is_active:
            raise AuthenticationFailed(
                "No active account found with the given credentials",
                code="no_active_account",
            )
        refresh = cast(RefreshToken, self.get_token(user))
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }
