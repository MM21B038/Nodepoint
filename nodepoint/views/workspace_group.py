from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView

from nodepoint.models import Document
from nodepoint.services import workspace as workspace_svc
from nodepoint.services import workspace_group as group_svc
from nodepoint.views.resource_lookup import resolve_group_response, resolve_workspace_response


def _group_mutation_error_response(exc: group_svc.GroupError) -> Response:
    if isinstance(exc, group_svc.GroupMembershipDenied):
        return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, group_svc.GroupNotFoundError):
        return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class CreateGroupAPIView(AuthenticatedAPIView):
    def post(self, request):
        name = request.data.get("name")
        tag = request.data.get("tag")
        description = request.data.get("description")
        try:
            tag = group_svc.validate_user_group_tag(tag)
            group = group_svc.create_group(
                name, owner=request.user, tag=tag, description=description
            )
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Group created successfully",
                "group": group_svc.serialize_group_for_api(group),
            },
            status=status.HTTP_201_CREATED,
        )


class ListGroupsAPIView(AuthenticatedAPIView):
    def get(self, request):
        from nodepoint.services import workspace_catalog
        from nodepoint.services.owner_scope import (
            OwnerNotAccessibleError,
            OwnerScopeError,
            parse_owner_id_from_request,
        )

        tag_filter = request.query_params.get("tag")
        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            owner_id = parse_owner_id_from_request(request)
        except (OwnerScopeError, OwnerNotAccessibleError) as exc:
            return Response(
                {"error": str(exc)},
                status=(
                    status.HTTP_403_FORBIDDEN
                    if isinstance(exc, OwnerNotAccessibleError)
                    else status.HTTP_400_BAD_REQUEST
                ),
            )
        try:
            payload = group_svc.list_groups(
                actor=request.user,
                tag_filter=tag_filter.strip() if tag_filter else None,
                owner_id=owner_id,
                page=page,
                page_size=page_size,
            )
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class GroupLookupAPIView(AuthenticatedAPIView):
    """GET /api/group/lookup/?name= — which owner(s) have a group with this name."""

    def get(self, request):
        from nodepoint.services.owner_scope import (
            OwnerNotAccessibleError,
            OwnerScopeError,
            parse_owner_id_from_request,
        )

        name = request.query_params.get("name")
        if not name or not str(name).strip():
            return Response(
                {"error": "name is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            owner_id = parse_owner_id_from_request(request)
        except (OwnerScopeError, OwnerNotAccessibleError) as exc:
            return Response(
                {"error": str(exc)},
                status=(
                    status.HTTP_403_FORBIDDEN
                    if isinstance(exc, OwnerNotAccessibleError)
                    else status.HTTP_400_BAD_REQUEST
                ),
            )
        tag_filter = request.query_params.get("tag")
        try:
            payload = group_svc.lookup_groups_by_name(
                name,
                actor=request.user,
                owner_id=owner_id,
                tag_filter=tag_filter.strip() if tag_filter else None,
            )
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class GroupMembersAPIView(AuthenticatedAPIView):
    def get(self, request, name):
        from nodepoint.services import workspace_catalog

        group, err = resolve_group_response(request, name)
        if err is not None:
            return err
        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            group_svc.list_group_members(
                group.name,
                actor=request.user,
                owner_id=group.owner_id,
                page=page,
                page_size=page_size,
            )
        )


class GroupDetailAPIView(AuthenticatedAPIView):
    def get(self, request, name):
        from nodepoint.services import workspace_catalog

        group, err = resolve_group_response(request, name)
        if err is not None:
            return err
        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            group_svc.get_group_detail(
                group.name,
                actor=request.user,
                owner_id=group.owner_id,
                page=page,
                page_size=page_size,
            )
        )

    def delete(self, request, name):
        group, err = resolve_group_response(request, name)
        if err is not None:
            return err
        group_svc.delete_group(
            group.name, actor=request.user, owner_id=group.owner_id
        )
        return Response({"message": "Group deleted successfully", "group": group.name})

    def patch(self, request, name):
        group, err = resolve_group_response(request, name)
        if err is not None:
            return err
        allowed = {"name", "description"}
        updates = {key: request.data[key] for key in allowed if key in request.data}
        if "tag" in request.data:
            return Response(
                {"error": "Group tag cannot be changed after create"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not updates:
            return Response(
                {"error": "No fields to update"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            group = group_svc.update_group(
                group.name,
                updates,
                actor=request.user,
                owner_id=group.owner_id,
            )
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        body = {
            "message": "Group updated successfully",
            "group": group_svc.serialize_group_for_api(group),
        }
        if group.name != name:
            body["previous_name"] = name
        return Response(body)


class AddWorkspaceToGroupAPIView(AuthenticatedAPIView):
    def post(self, request, name):
        workspace_name = request.data.get("workspace_name")
        if not workspace_name:
            return Response(
                {"error": "workspace_name is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        group, err = resolve_group_response(request, name)
        if err is not None:
            return err
        workspace, err = resolve_workspace_response(request, workspace_name)
        if err is not None:
            return err
        try:
            group_svc.add_workspace_to_group(
                group.name,
                workspace,
                actor=request.user,
                owner_id=group.owner_id,
            )
        except group_svc.GroupError as exc:
            return _group_mutation_error_response(exc)
        return Response(
            {
                "message": "Workspace added to group",
                "group": group.name,
                "workspace": workspace.name,
            }
        )


class RemoveWorkspaceFromGroupAPIView(AuthenticatedAPIView):
    def delete(self, request, name, workspace_name):
        group, err = resolve_group_response(request, name)
        if err is not None:
            return err
        workspace, err = resolve_workspace_response(request, workspace_name)
        if err is not None:
            return err
        try:
            group_svc.remove_workspace_from_group(
                group.name,
                workspace,
                actor=request.user,
                owner_id=group.owner_id,
            )
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Workspace removed from group",
                "group": group.name,
                "workspace": workspace.name,
            }
        )


class AddFileToGroupAPIView(AuthenticatedAPIView):
    def post(self, request, name):
        group, err = resolve_group_response(request, name)
        if err is not None:
            return err
        document_id = request.data.get("document_id")
        workspace_name = request.data.get("workspace_name")
        file_name = request.data.get("file_name")
        try:
            if document_id:
                document = Document.objects.select_related("workspace").get(
                    id=document_id
                )
            elif workspace_name and file_name:
                workspace, ws_err = resolve_workspace_response(
                    request, workspace_name
                )
                if ws_err is not None:
                    return ws_err
                document = Document.objects.select_related("workspace").get(
                    workspace=workspace,
                    file_name=file_name,
                )
            else:
                return Response(
                    {
                        "error": "Provide document_id or workspace_name and file_name"
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Document.DoesNotExist:
            return Response(
                {"error": "Document not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            group_svc.add_document_to_group(
                group.name,
                document,
                actor=request.user,
                owner_id=group.owner_id,
            )
        except group_svc.GroupError as exc:
            return _group_mutation_error_response(exc)
        return Response(
            {
                "message": "File added to group",
                "group": group.name,
                "document_id": str(document.id),
                "workspace": document.workspace.name,
                "file_name": document.file_name,
            }
        )


class RemoveFileFromGroupAPIView(AuthenticatedAPIView):
    def delete(self, request, name, document_id):
        group, err = resolve_group_response(request, name)
        if err is not None:
            return err
        try:
            group_svc.remove_document_from_group(
                group.name,
                document_id,
                actor=request.user,
                owner_id=group.owner_id,
            )
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "File removed from group",
                "group": group.name,
                "document_id": str(document_id),
            }
        )


class GroupAddOptionsAPIView(AuthenticatedAPIView):
    def get(self, request, name):
        from nodepoint.services import workspace_catalog
        from nodepoint.services.owner_scope import (
            OwnerNotAccessibleError,
            OwnerScopeError,
            parse_owner_id,
        )

        group, err = resolve_group_response(request, name)
        if err is not None:
            return err
        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        candidate_owner_id = None
        raw_candidate = request.query_params.get("candidate_owner_id")
        if raw_candidate is not None and str(raw_candidate).strip() != "":
            try:
                candidate_owner_id = parse_owner_id(
                    actor=request.user,
                    owner_id_raw=raw_candidate,
                )
            except (OwnerScopeError, OwnerNotAccessibleError) as exc:
                return Response(
                    {"error": str(exc)},
                    status=(
                        status.HTTP_403_FORBIDDEN
                        if isinstance(exc, OwnerNotAccessibleError)
                        else status.HTTP_400_BAD_REQUEST
                    ),
                )
        search = request.query_params.get("search")
        try:
            payload = group_svc.list_group_add_options(
                group,
                actor=request.user,
                page=page,
                page_size=page_size,
                search=search,
                candidate_owner_id=candidate_owner_id,
            )
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class WorkspaceGroupOptionsAPIView(AuthenticatedAPIView):
    def get(self, request, workspace_name):
        from nodepoint.services import workspace_catalog

        workspace, err = resolve_workspace_response(request, workspace_name)
        if err is not None:
            return err
        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        search = request.query_params.get("search")
        return Response(
            group_svc.list_workspace_group_options(
                workspace,
                actor=request.user,
                page=page,
                page_size=page_size,
                search=search,
            )
        )
