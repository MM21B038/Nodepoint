"""Authentication, RBAC, API key, lifecycle, and usage tests."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from nodepoint.auth.account_lifecycle import schedule_user_deletion
from nodepoint.auth.api_keys import create_api_key
from nodepoint.auth.password_validators import NodepointPasswordValidator
from nodepoint.auth.users import User, ensure_profile
from nodepoint.enums import AccountStatus, UserRole
from nodepoint.enums import Status
from nodepoint.models import ApiKey, ApiUsageLog, Document, Workspace, WorkspaceGroup
from nodepoint.test_helpers import authenticated_client, create_test_user, create_test_workspace

VALID_PASSWORD = "SecurePass123!"
USER_GROUP_WRITE_SCOPES = [
    "workspace:read",
    "document:read",
    "chat:read",
    "group:read",
    "group:write",
    "kg:read",
    "preprocess:read",
    "preprocess:write",
]


class AuthRBACTests(APITestCase):
    def test_password_validator_rejects_weak(self):
        validator = NodepointPasswordValidator()
        with self.assertRaises(ValidationError):
            validator.validate("short")
        with self.assertRaises(ValidationError):
            validator.validate("alllowercase123!")
        validator.validate(VALID_PASSWORD)

    def test_register_user_and_admin(self):
        client, _ = authenticated_client()
        for role in (UserRole.USER, UserRole.ADMIN):
            resp = client.post(
                "/api/auth/register/",
                {
                    "username": f"reg-{role}",
                    "password": VALID_PASSWORD,
                    "role": role,
                },
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
            self.assertEqual(resp.json()["role"], role)

    def test_register_rejects_weak_password(self):
        client, _ = authenticated_client()
        resp = client.post(
            "/api/auth/register/",
            {"username": "weak-user", "password": "password", "role": UserRole.USER},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_returns_jwt(self):
        create_test_user(username="login-user", password=VALID_PASSWORD)
        client, _ = authenticated_client()
        resp = client.post(
            "/api/auth/token/",
            {"username": "login-user", "password": VALID_PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.json())
        self.assertIn("refresh", resp.json())

    def test_admin_creates_managed_user(self):
        admin = create_test_user(username="admin1", role=UserRole.ADMIN)
        client, _ = authenticated_client(admin)
        resp = client.post(
            "/api/auth/users/",
            {"username": "child", "password": VALID_PASSWORD, "role": UserRole.USER},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["managed_by"], admin.pk)

    def test_user_cannot_see_other_workspace(self):
        user_a = create_test_user(username="user-a")
        user_b = create_test_user(username="user-b")
        ws = create_test_workspace(name="private-ws", owner=user_b)
        client, _ = authenticated_client(user_a)
        resp = client.get("/api/workspace/list/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [w["name"] for w in resp.json()["workspaces"]]
        self.assertNotIn(ws.name, names)

    def test_api_key_scope_allowed_and_denied(self):
        user = create_test_user(username="key-user")
        create_test_workspace(name="key-ws", owner=user)
        api_key, full_key = create_api_key(
            user=user,
            created_by=user,
            name="read-only",
            scopes=["workspace:read"],
            expiry_preset="3_months",
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Api-Key {full_key}")
        ok = client.get("/api/workspace/list/")
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        denied = client.post(
            "/api/workspace/create/",
            {"name": "blocked-ws"},
            format="json",
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        api_key.delete()

    def test_expired_api_key_rejected(self):
        user = create_test_user(username="exp-user")
        api_key, full_key = create_api_key(
            user=user,
            created_by=user,
            name="expired",
            scopes=["workspace:read"],
            expiry_preset="3_months",
        )
        ApiKey.objects.filter(pk=api_key.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Api-Key {full_key}")
        resp = client.get("/api/workspace/list/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_schedule_delete_and_recover(self):
        user = create_test_user(username="del-user", password=VALID_PASSWORD)
        schedule_user_deletion(user, requested_by=user)
        client, _ = authenticated_client()
        login = client.post(
            "/api/auth/token/",
            {"username": "del-user", "password": VALID_PASSWORD},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_401_UNAUTHORIZED)
        recover = client.post(
            "/api/auth/account/recover/",
            {
                "username": "del-user",
                "password": VALID_PASSWORD,
                "role": UserRole.USER,
            },
            format="json",
        )
        self.assertEqual(recover.status_code, status.HTTP_200_OK)
        login2 = client.post(
            "/api/auth/token/",
            {"username": "del-user", "password": VALID_PASSWORD},
            format="json",
        )
        self.assertEqual(login2.status_code, status.HTTP_200_OK)

    def test_recover_fails_after_purge_date(self):
        user = create_test_user(username="late-user", password=VALID_PASSWORD)
        profile = ensure_profile(user)
        schedule_user_deletion(user, requested_by=user)
        profile.purge_scheduled_at = timezone.now() - timedelta(days=1)
        profile.save(update_fields=["purge_scheduled_at"])
        client, _ = authenticated_client()
        resp = client.post(
            "/api/auth/account/recover/",
            {
                "username": "late-user",
                "password": VALID_PASSWORD,
                "role": UserRole.USER,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_jwt_allowed_scopes_enforced(self):
        user = create_test_user(username="scoped-user", role=UserRole.USER)
        profile = ensure_profile(user)
        profile.allowed_scopes = ["workspace:read"]
        profile.save(update_fields=["allowed_scopes"])
        client, _ = authenticated_client(user)
        ok = client.get("/api/workspace/list/")
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        denied = client.post(
            "/api/workspace/create/",
            {"name": "no-scope-ws"},
            format="json",
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_sets_user_allowed_scopes(self):
        admin = create_test_user(username="scope-admin", role=UserRole.ADMIN)
        child = create_test_user(username="scope-child", role=UserRole.USER, managed_by=admin)
        client, _ = authenticated_client(admin)
        resp = client.patch(
            f"/api/auth/users/{child.pk}/allowed-scopes/",
            {"allowed_scopes": ["workspace:read", "chat:read"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            resp.json()["allowed_scopes"],
            ["workspace:read", "chat:read"],
        )

    def test_patch_user_with_allowed_scopes_persists(self):
        admin = create_test_user(username="scope-patch-admin", role=UserRole.ADMIN)
        child = create_test_user(username="scope-patch-child", role=UserRole.USER, managed_by=admin)
        client, _ = authenticated_client(admin)
        resp = client.patch(
            f"/api/auth/users/{child.pk}/",
            {"allowed_scopes": ["workspace:read", "chat:read"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            resp.json()["allowed_scopes"],
            ["workspace:read", "chat:read"],
        )

    def test_allowed_scopes_accepts_access_alias(self):
        admin = create_test_user(username="scope-alias-admin", role=UserRole.ADMIN)
        child = create_test_user(username="scope-alias-child", role=UserRole.USER, managed_by=admin)
        client, _ = authenticated_client(admin)
        resp = client.patch(
            f"/api/auth/users/{child.pk}/allowed-scopes/",
            {"access": ["workspace:read"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()["allowed_scopes"], ["workspace:read"])

    def test_allowed_scopes_unknown_code_returns_400(self):
        admin = create_test_user(username="scope-bad-admin", role=UserRole.ADMIN)
        child = create_test_user(username="scope-bad-child", role=UserRole.USER, managed_by=admin)
        client, _ = authenticated_client(admin)
        resp = client.patch(
            f"/api/auth/users/{child.pk}/allowed-scopes/",
            {"allowed_scopes": ["not-a-real-scope"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Unknown scopes", str(resp.json()))

    def test_allowed_scopes_wrong_key_only_returns_400(self):
        admin = create_test_user(username="scope-key-admin", role=UserRole.ADMIN)
        child = create_test_user(username="scope-key-child", role=UserRole.USER, managed_by=admin)
        client, _ = authenticated_client(admin)
        resp = client.patch(
            f"/api/auth/users/{child.pk}/allowed-scopes/",
            {"permissions": ["workspace:read"]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_user_with_invalid_scopes_returns_400(self):
        admin = create_test_user(username="scope-create-admin", role=UserRole.ADMIN)
        client, _ = authenticated_client(admin)
        resp = client.post(
            "/api/auth/users/",
            {
                "username": "scope-new-user",
                "password": VALID_PASSWORD,
                "role": UserRole.USER,
                "allowed_scopes": ["bogus:scope"],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Unknown scopes", str(resp.json()))

    def test_usage_logged_on_api_call(self):
        user = create_test_user(username="usage-user")
        create_test_workspace(name="usage-ws", owner=user)
        client, _ = authenticated_client(user)
        before = ApiUsageLog.objects.filter(user=user).count()
        client.get("/api/workspace/list/")
        after = ApiUsageLog.objects.filter(user=user).count()
        self.assertEqual(after, before + 1)

    def test_admin_views_managed_user_usage(self):
        admin = create_test_user(username="usage-admin", role=UserRole.ADMIN)
        child = create_test_user(username="usage-child", role=UserRole.USER, managed_by=admin)
        child_client, _ = authenticated_client(child)
        child_client.get("/api/auth/me/")
        admin_client, _ = authenticated_client(admin)
        resp = admin_client.get(f"/api/auth/usage/users/{child.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.json()["total_calls"], 1)

    @patch("nodepoint.auth.account_lifecycle.purge_due_accounts")
    def test_purge_command_invokes_service(self, mock_purge):
        from django.core.management import call_command

        mock_purge.return_value = [1]
        call_command("purge_deleted_users")


class UserLifecycleTests(APITestCase):
    def test_deactivate_and_purge_user(self):
        admin = create_test_user(username="life-admin", role=UserRole.ADMIN)
        child = create_test_user(username="life-child", role=UserRole.USER, managed_by=admin)
        create_test_workspace(name="child-ws", owner=child)
        client, _ = authenticated_client(admin)

        deactivate = client.patch(
            f"/api/auth/users/{child.pk}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(deactivate.status_code, status.HTTP_200_OK)
        self.assertEqual(deactivate.json()["account_state"], "inactive")
        self.assertTrue(deactivate.json()["can_purge_permanently"])

        purge = client.post(f"/api/auth/users/{child.pk}/purge/")
        self.assertEqual(purge.status_code, status.HTTP_200_OK)
        self.assertFalse(purge.json()["recoverable"])
        self.assertFalse(User.objects.filter(pk=child.pk).exists())

    def test_list_users_filter_by_account_state(self):
        admin = create_test_user(username="filter-admin", role=UserRole.ADMIN)
        active = create_test_user(username="filter-active", role=UserRole.USER, managed_by=admin)
        inactive = create_test_user(username="filter-inactive", role=UserRole.USER, managed_by=admin)
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        client, _ = authenticated_client(admin)

        resp = client.get("/api/auth/users/?account_state=inactive")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [u["username"] for u in resp.json()["users"]]
        self.assertIn("filter-inactive", names)
        self.assertNotIn("filter-active", names)

    def test_api_key_deactivate_then_delete(self):
        user = create_test_user(username="key-owner")
        admin = create_test_user(username="key-admin", role=UserRole.SUPERADMIN)
        from nodepoint.auth.api_keys import create_api_key

        api_key, full_key = create_api_key(
            user=user,
            created_by=admin,
            name="test-key",
            scopes=["workspace:read"],
            expiry_preset="3_months",
        )
        client, _ = authenticated_client(admin)

        blocked = client.delete(f"/api/auth/api-keys/{api_key.pk}/")
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)

        off = client.patch(
            f"/api/auth/api-keys/{api_key.pk}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(off.status_code, status.HTTP_200_OK)
        self.assertEqual(off.json()["key_status"], "inactive")

        deleted = client.delete(f"/api/auth/api-keys/{api_key.pk}/")
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ApiKey.objects.filter(pk=api_key.pk).exists())


class PasswordAndNamingTests(APITestCase):
    def test_me_change_password(self):
        user = create_test_user(username="pwd-user", password=VALID_PASSWORD)
        client, _ = authenticated_client(user)
        resp = client.post(
            "/api/auth/me/password/",
            {
                "current_password": VALID_PASSWORD,
                "new_password": "NewSecurePass123!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        client.logout()
        login = client.post(
            "/api/auth/token/",
            {"username": "pwd-user", "password": "NewSecurePass123!"},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_me_change_password_wrong_current(self):
        user = create_test_user(username="pwd-bad", password=VALID_PASSWORD)
        client, _ = authenticated_client(user)
        resp = client.post(
            "/api/auth/me/password/",
            {
                "current_password": "WrongPass123!",
                "new_password": "NewSecurePass123!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_patches_managed_user_password(self):
        admin = create_test_user(username="pwd-admin", role=UserRole.ADMIN)
        child = create_test_user(username="pwd-child", role=UserRole.USER, managed_by=admin)
        client, _ = authenticated_client(admin)
        resp = client.patch(
            f"/api/auth/users/{child.pk}/",
            {"password": "ChildNewPass123!"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_duplicate_workspace_names_per_owner(self):
        user_a = create_test_user(username="owner-a")
        user_b = create_test_user(username="owner-b")
        create_test_workspace(name="SharedName", owner=user_a)
        create_test_workspace(name="SharedName", owner=user_b)
        superadmin = create_test_user(username="sa-naming", role=UserRole.SUPERADMIN)
        client, _ = authenticated_client(superadmin)
        resp = client.get("/api/workspace/list/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [w["name"] for w in resp.json()["workspaces"]]
        self.assertEqual(names.count("SharedName"), 2)
        owners = {w["owner_username"] for w in resp.json()["workspaces"] if w["name"] == "SharedName"}
        self.assertEqual(owners, {"owner-a", "owner-b"})

    def test_ambiguous_workspace_name_returns_candidates(self):
        user_a = create_test_user(username="ambig-a")
        user_b = create_test_user(username="ambig-b")
        create_test_workspace(name="123", owner=user_a)
        create_test_workspace(name="123", owner=user_b)
        superadmin = create_test_user(username="sa-ambig", role=UserRole.SUPERADMIN)
        client, _ = authenticated_client(superadmin)
        resp = client.get("/api/chat/123/sessions/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        body = resp.json()
        self.assertIn("owner_id", body["error"])
        self.assertEqual(len(body["candidates"]), 2)

    def test_workspace_resolved_with_owner_id_query(self):
        user_a = create_test_user(username="resolve-a")
        user_b = create_test_user(username="resolve-b")
        create_test_workspace(name="123", owner=user_a)
        create_test_workspace(name="123", owner=user_b)
        superadmin = create_test_user(username="sa-resolve", role=UserRole.SUPERADMIN)
        client, _ = authenticated_client(superadmin)
        resp = client.get(
            "/api/chat/123/sessions/",
            {"owner_id": user_a.pk},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()["workspace"], "123")

    def test_ambiguous_group_name_returns_candidates(self):
        user_a = create_test_user(username="grp-a")
        user_b = create_test_user(username="grp-b")
        WorkspaceGroup.objects.create(name="team", owner=user_a)
        WorkspaceGroup.objects.create(name="team", owner=user_b)
        superadmin = create_test_user(username="sa-grp", role=UserRole.SUPERADMIN)
        client, _ = authenticated_client(superadmin)
        resp = client.get("/api/chat/group/team/sessions/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("owner_id", resp.json()["error"])
        self.assertEqual(len(resp.json()["candidates"]), 2)

    def test_kg_group_scope_ambiguous_returns_candidates(self):
        user_a = create_test_user(username="kg-a")
        user_b = create_test_user(username="kg-b")
        WorkspaceGroup.objects.create(name="kggrp", owner=user_a)
        WorkspaceGroup.objects.create(name="kggrp", owner=user_b)
        superadmin = create_test_user(username="sa-kg", role=UserRole.SUPERADMIN)
        client, _ = authenticated_client(superadmin)
        resp = client.get("/api/chat/summary/?group=kggrp")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(resp.json()["candidates"]), 2)

    def _two_workspaces_named_123(self):
        user_a = create_test_user(username="dup-a")
        user_b = create_test_user(username="dup-b")
        create_test_workspace(name="123", owner=user_a)
        create_test_workspace(name="123", owner=user_b)
        superadmin = create_test_user(username="sa-dup", role=UserRole.SUPERADMIN)
        client, _ = authenticated_client(superadmin)
        return client, user_a, user_b

    def test_patch_workspace_tag_with_owner_id(self):
        client, user_a, _user_b = self._two_workspaces_named_123()
        resp = client.patch(
            "/api/workspace/update/123/",
            {"tag": "notes", "owner_id": user_a.pk},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()["workspace"]["tag"], "notes")
        self.assertEqual(resp.json()["workspace"]["owner_id"], user_a.pk)

    def test_list_documents_ambiguous_and_resolved(self):
        client, user_a, _user_b = self._two_workspaces_named_123()
        resp = client.get("/api/document/123/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(resp.json()["candidates"]), 2)
        resp = client.get("/api/document/123/", {"owner_id": user_a.pk})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()["workspace"], "123")

    def test_knowledge_graph_workspace_with_owner_id(self):
        client, user_a, _user_b = self._two_workspaces_named_123()
        resp = client.get(
            "/api/knowledge-graph/",
            {"workspace_name": "123", "owner_id": user_a.pk},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()["workspace"], "123")

    def test_queue_status_workspace_with_owner_id(self):
        client, user_a, _user_b = self._two_workspaces_named_123()
        resp = client.get("/api/preprocess/queue-status/", {"workspace": "123"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        resp = client.get(
            "/api/preprocess/queue-status/",
            {"workspace": "123", "owner_id": user_a.pk},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()["workspace_filter"], "123")


class PreprocessVisibilityTests(APITestCase):
    def test_workspaces_summary_excludes_other_users_workspace(self):
        user_a = create_test_user(username="pre-a")
        user_b = create_test_user(username="pre-b")
        create_test_workspace(name="mine-ws", owner=user_a)
        other_ws = create_test_workspace(name="other-ws", owner=user_b)
        Document.objects.create(
            workspace=other_ws,
            file_name="secret.md",
            status=Status.PENDING,
        )
        client, _ = authenticated_client(user_a)
        resp = client.get("/api/preprocess/workspaces-summary/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [row["workspace"] for row in resp.json()["workspaces"]]
        self.assertNotIn("other-ws", names)

    def test_queue_status_workspace_filter_404_when_not_visible(self):
        user_a = create_test_user(username="pre-filter-a")
        user_b = create_test_user(username="pre-filter-b")
        create_test_workspace(name="hidden-ws", owner=user_b)
        client, _ = authenticated_client(user_a)
        resp = client.get("/api/preprocess/queue-status/?workspace=hidden-ws")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_preprocess_status_404_for_other_users_workspace(self):
        user_a = create_test_user(username="pre-status-a")
        user_b = create_test_user(username="pre-status-b")
        create_test_workspace(name="status-hidden", owner=user_b)
        client, _ = authenticated_client(user_a)
        resp = client.get("/api/workspace/status-hidden/preprocess-status/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_preprocess_post_requires_auth(self):
        create_test_workspace(name="anon-ws")
        client = APIClient()
        resp = client.post("/api/workspace/preprocess/anon-ws/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class GroupMembershipPolicyTests(APITestCase):
    def _create_group(self, owner, name, **kwargs):
        from nodepoint.services import workspace_group as group_svc

        return group_svc.create_group(name, owner=owner, **kwargs)

    def _add_workspace_to_group(self, client, group_name, workspace_name, **query):
        url = f"/api/group/{group_name}/workspaces/"
        if query:
            params = "&".join(f"{key}={value}" for key, value in query.items())
            url = f"{url}?{params}"
        return client.post(
            url,
            {"workspace_name": workspace_name},
            format="json",
        )

    def test_user_cannot_add_other_users_workspace_to_own_group(self):
        user_a = create_test_user(
            username="grp-user-a", allowed_scopes=USER_GROUP_WRITE_SCOPES
        )
        user_b = create_test_user(username="grp-user-b")
        create_test_workspace(name="other-ws", owner=user_b)
        client, _ = authenticated_client(user_a)
        create_test_workspace(name="mine-ws", owner=user_a)
        self._create_group(user_a, "my-group")
        resp = self._add_workspace_to_group(client, "my-group", "other-ws")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_can_add_managed_user_workspace_to_admin_owned_group(self):
        admin = create_test_user(username="grp-admin", role=UserRole.ADMIN)
        alice = create_test_user(username="grp-alice", role=UserRole.USER, managed_by=admin)
        create_test_workspace(name="alice-ws", owner=alice)
        client, _ = authenticated_client(admin)
        self._create_group(admin, "aggregate")
        resp = self._add_workspace_to_group(client, "aggregate", "alice-ws")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)

    def test_admin_cannot_add_workspace_to_other_users_group(self):
        admin = create_test_user(username="grp-admin2", role=UserRole.ADMIN)
        alice = create_test_user(username="grp-alice2", role=UserRole.USER, managed_by=admin)
        bob = create_test_user(username="grp-bob", role=UserRole.USER, managed_by=admin)
        create_test_workspace(name="bob-ws", owner=bob)
        alice_client, _ = authenticated_client(alice)
        self._create_group(alice, "alice-group")
        admin_client, _ = authenticated_client(admin)
        resp = self._add_workspace_to_group(
            admin_client,
            "alice-group",
            "bob-ws",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_group_add_options_excludes_existing_members(self):
        user = create_test_user(
            username="opts-user", allowed_scopes=USER_GROUP_WRITE_SCOPES
        )
        ws_in = create_test_workspace(name="in-group", owner=user)
        create_test_workspace(name="not-in-group", owner=user)
        client, _ = authenticated_client(user)
        self._create_group(user, "picker-group")
        add_resp = self._add_workspace_to_group(client, "picker-group", "in-group")
        self.assertEqual(add_resp.status_code, status.HTTP_200_OK)
        resp = client.get("/api/group/picker-group/add-options/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [row["name"] for row in resp.json()["items"]]
        self.assertNotIn("in-group", names)
        self.assertIn("not-in-group", names)

    def test_admin_aggregate_add_options_lists_managed_workspaces(self):
        admin = create_test_user(username="opts-admin", role=UserRole.ADMIN)
        alice = create_test_user(username="opts-alice", role=UserRole.USER, managed_by=admin)
        bob = create_test_user(username="opts-bob", role=UserRole.USER, managed_by=admin)
        create_test_workspace(name="alice-ws", owner=alice)
        create_test_workspace(name="bob-ws", owner=bob)
        client, _ = authenticated_client(admin)
        self._create_group(admin, "all-research")
        resp = client.get(
            "/api/group/all-research/add-options/",
            {"owner_id": admin.pk},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {row["name"] for row in resp.json()["items"]}
        owner_ids = {row["owner_id"] for row in resp.json()["items"]}
        self.assertEqual(names, {"alice-ws", "bob-ws"})
        self.assertEqual(owner_ids, {alice.pk, bob.pk})

    def test_workspace_group_options_marks_membership_and_eligibility(self):
        user = create_test_user(
            username="ws-opts-user", allowed_scopes=USER_GROUP_WRITE_SCOPES
        )
        ws = create_test_workspace(name="target-ws", owner=user)
        client, _ = authenticated_client(user)
        self._create_group(user, "eligible")
        self._create_group(user, "joined")
        join_resp = self._add_workspace_to_group(client, "joined", "target-ws")
        self.assertEqual(join_resp.status_code, status.HTTP_200_OK)
        resp = client.get("/api/workspace/target-ws/group-options/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        by_name = {row["name"]: row for row in resp.json()["groups"]}
        self.assertIn("eligible", by_name)
        self.assertFalse(by_name["eligible"]["already_member"])
        self.assertIn("joined", by_name)
        self.assertTrue(by_name["joined"]["already_member"])

    def test_files_group_add_options_respects_owner_policy(self):
        admin = create_test_user(username="file-admin", role=UserRole.ADMIN)
        alice = create_test_user(username="file-alice", role=UserRole.USER, managed_by=admin)
        bob = create_test_user(username="file-bob", role=UserRole.USER, managed_by=admin)
        alice_ws = create_test_workspace(name="file-alice-ws", owner=alice)
        bob_ws = create_test_workspace(name="file-bob-ws", owner=bob)
        Document.objects.create(workspace=alice_ws, file_name="alice.md", status=Status.PENDING)
        Document.objects.create(workspace=bob_ws, file_name="bob.md", status=Status.PENDING)
        alice_client, _ = authenticated_client(alice)
        self._create_group(alice, "alice-files", tag="files")
        admin_client, _ = authenticated_client(admin)
        resp = admin_client.get(
            "/api/group/alice-files/add-options/",
            {"owner_id": alice.pk},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        file_names = {row["file_name"] for row in resp.json()["items"]}
        self.assertEqual(file_names, {"alice.md"})
        self.assertNotIn("bob.md", file_names)


class GroupOwnerLookupTests(APITestCase):
    def test_group_lookup_lists_owners_for_duplicate_names(self):
        user_a = create_test_user(username="lookup-a")
        user_b = create_test_user(username="lookup-b")
        WorkspaceGroup.objects.create(name="team", owner=user_a, tag="workspace")
        WorkspaceGroup.objects.create(name="team", owner=user_b, tag="workspace")
        superadmin = create_test_user(username="lookup-sa", role=UserRole.SUPERADMIN)
        client, _ = authenticated_client(superadmin)
        resp = client.get("/api/group/lookup/", {"name": "team"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.json()
        self.assertTrue(body["ambiguous"])
        self.assertEqual(len(body["matches"]), 2)
        owners = {row["owner_username"] for row in body["matches"]}
        self.assertEqual(owners, {"lookup-a", "lookup-b"})

    def test_group_lookup_with_owner_id_returns_single_match(self):
        user_a = create_test_user(username="lookup2-a")
        user_b = create_test_user(username="lookup2-b")
        WorkspaceGroup.objects.create(name="shared", owner=user_a)
        WorkspaceGroup.objects.create(name="shared", owner=user_b)
        superadmin = create_test_user(username="lookup2-sa", role=UserRole.SUPERADMIN)
        client, _ = authenticated_client(superadmin)
        resp = client.get(
            "/api/group/lookup/",
            {"name": "shared", "owner_id": user_a.pk},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.json()
        self.assertFalse(body["ambiguous"])
        self.assertEqual(len(body["matches"]), 1)
        self.assertEqual(body["matches"][0]["owner_id"], user_a.pk)

    def test_group_list_filter_by_owner_id(self):
        admin = create_test_user(username="list-admin", role=UserRole.ADMIN)
        alice = create_test_user(username="list-alice", role=UserRole.USER, managed_by=admin)
        bob = create_test_user(username="list-bob", role=UserRole.USER, managed_by=admin)
        WorkspaceGroup.objects.create(name="alice-g1", owner=alice)
        WorkspaceGroup.objects.create(name="alice-g2", owner=alice)
        WorkspaceGroup.objects.create(name="bob-g1", owner=bob)
        client, _ = authenticated_client(admin)
        resp = client.get("/api/group/list/", {"owner_id": alice.pk})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {row["name"] for row in resp.json()["groups"]}
        self.assertEqual(names, {"alice-g1", "alice-g2"})

    def test_workspace_lookup_lists_owners_for_duplicate_names(self):
        user_a = create_test_user(username="ws-lookup-a")
        user_b = create_test_user(username="ws-lookup-b")
        create_test_workspace(name="dup", owner=user_a)
        create_test_workspace(name="dup", owner=user_b)
        superadmin = create_test_user(username="ws-lookup-sa", role=UserRole.SUPERADMIN)
        client, _ = authenticated_client(superadmin)
        resp = client.get("/api/workspace/lookup/", {"name": "dup"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.json()["ambiguous"])
        self.assertEqual(len(resp.json()["matches"]), 2)
