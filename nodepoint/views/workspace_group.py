from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Document, KnowledgeEntity, KnowledgeRelation, Workspace
from nodepoint.services import workspace_group as group_svc


class CreateGroupAPIView(APIView):
    def post(self, request):
        name = request.data.get("name")
        tag = request.data.get("tag")
        description = request.data.get("description")
        try:
            group = group_svc.create_group(name, tag=tag, description=description)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Group created successfully",
                "group": group_svc.serialize_group_for_api(group),
            },
            status=status.HTTP_201_CREATED,
        )


class ListGroupsAPIView(APIView):
    def get(self, request):
        from nodepoint.services import workspace_catalog

        tag_filter = request.query_params.get("tag")
        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = group_svc.list_groups(
                tag_filter=tag_filter.strip() if tag_filter else None,
                page=page,
                page_size=page_size,
            )
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class GroupMembersAPIView(APIView):
    def get(self, request, name):
        from nodepoint.services import workspace_catalog

        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return Response(
                group_svc.list_group_members(name, page=page, page_size=page_size)
            )
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)


class GroupDetailAPIView(APIView):
    def get(self, request, name):
        from nodepoint.services import workspace_catalog

        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return Response(
                group_svc.get_group_detail(name, page=page, page_size=page_size)
            )
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request, name):
        try:
            group_svc.delete_group(name)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response({"message": "Group deleted successfully", "group": name})

    def patch(self, request, name):
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
            group = group_svc.update_group(name, updates)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        body = {
            "message": "Group updated successfully",
            "group": group_svc.serialize_group_for_api(group),
        }
        if group.name != name:
            body["previous_name"] = name
        return Response(body)


class AddWorkspaceToGroupAPIView(APIView):
    def post(self, request, name):
        workspace_name = request.data.get("workspace_name")
        if not workspace_name:
            return Response(
                {"error": "workspace_name is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            workspace = Workspace.objects.get(name=workspace_name)
        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            group_svc.add_workspace_to_group(name, workspace)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Workspace added to group",
                "group": name,
                "workspace": workspace.name,
            }
        )


class RemoveWorkspaceFromGroupAPIView(APIView):
    def delete(self, request, name, workspace_name):
        try:
            workspace = Workspace.objects.get(name=workspace_name)
        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            group_svc.remove_workspace_from_group(name, workspace)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Workspace removed from group",
                "group": name,
                "workspace": workspace.name,
            }
        )


class AddFileToGroupAPIView(APIView):
    def post(self, request, name):
        document_id = request.data.get("document_id")
        workspace_name = request.data.get("workspace_name")
        file_name = request.data.get("file_name")
        try:
            if document_id:
                document = Document.objects.select_related("workspace").get(
                    id=document_id
                )
            elif workspace_name and file_name:
                document = Document.objects.select_related("workspace").get(
                    workspace__name=workspace_name,
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
            group_svc.add_document_to_group(name, document)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "File added to group",
                "group": name,
                "document_id": str(document.id),
                "workspace": document.workspace.name,
                "file_name": document.file_name,
            }
        )


class RemoveFileFromGroupAPIView(APIView):
    def delete(self, request, name, document_id):
        try:
            group_svc.remove_document_from_group(name, document_id)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "File removed from group",
                "group": name,
                "document_id": str(document_id),
            }
        )


class AddEntityToGroupAPIView(APIView):
    def post(self, request, name):
        entity_id = request.data.get("entity_id")
        if not entity_id:
            return Response(
                {"error": "entity_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            entity = KnowledgeEntity.objects.select_related(
                "document", "document__workspace"
            ).get(id=entity_id)
        except KnowledgeEntity.DoesNotExist:
            return Response(
                {"error": "Entity not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            group_svc.add_entity_to_group(name, entity)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Entity added to group",
                "group": name,
                "entity_id": str(entity.id),
                "name": entity.name,
            }
        )


class RemoveEntityFromGroupAPIView(APIView):
    def delete(self, request, name, entity_id):
        try:
            group_svc.remove_entity_from_group(name, entity_id)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Entity removed from group",
                "group": name,
                "entity_id": str(entity_id),
            }
        )


class AddRelationToGroupAPIView(APIView):
    def post(self, request, name):
        relation_id = request.data.get("relation_id")
        if not relation_id:
            return Response(
                {"error": "relation_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            relation = KnowledgeRelation.objects.select_related(
                "document", "document__workspace", "source", "target"
            ).get(id=relation_id)
        except KnowledgeRelation.DoesNotExist:
            return Response(
                {"error": "Relation not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            group_svc.add_relation_to_group(name, relation)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Relation added to group",
                "group": name,
                "relation_id": str(relation.id),
            }
        )


class RemoveRelationFromGroupAPIView(APIView):
    def delete(self, request, name, relation_id):
        try:
            group_svc.remove_relation_from_group(name, relation_id)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Relation removed from group",
                "group": name,
                "relation_id": str(relation_id),
            }
        )
