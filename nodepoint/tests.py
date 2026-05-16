import tempfile
import uuid
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from nodepoint.backend.kg_builder import ingest_knowledge_graph
from nodepoint.enums import Status
from nodepoint.models import (
    ChatBranch,
    Conversation,
    Document,
    KnowledgeEntity,
    KnowledgeRelation,
    Workspace,
)
from nodepoint.registry.schema import Entity, KnowledgeGraph, Relation
from nodepoint.services.document import doc_preprocess


class KnowledgeGraphIngestTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="test-ws")
        self.document = Document.objects.create(
            workspace=self.workspace,
            file_name="sample.md",
            file=SimpleUploadedFile("sample.md", b"# hello"),
        )

    def test_ingest_resolves_relation_fks_by_entity_name(self):
        graph = KnowledgeGraph(
            entities=[
                Entity(name="Alice", type="PER", attributes={}),
                Entity(name="Acme", type="ORG", attributes={}),
            ],
            relations=[
                Relation(
                    source="Alice",
                    target="Acme",
                    type_description="works at",
                    description="Alice works at Acme Corp.",
                ),
            ],
        )

        ok, entity_ids, relation_ids = ingest_knowledge_graph(self.document, graph)
        self.assertTrue(ok)
        self.assertEqual(len(entity_ids), 2)
        self.assertEqual(len(relation_ids), 1)

        relation = KnowledgeRelation.objects.get(id=relation_ids[0])
        self.assertEqual(relation.source.name, "Alice")
        self.assertEqual(relation.target.name, "Acme")

    def test_ingest_is_idempotent(self):
        graph = KnowledgeGraph(
            entities=[Entity(name="Node", type="TECH", attributes={})],
            relations=[],
        )
        ingest_knowledge_graph(self.document, graph)
        ingest_knowledge_graph(self.document, graph)
        self.assertEqual(
            KnowledgeEntity.objects.filter(document=self.document).count(),
            1,
        )


class DocPreprocessTests(TestCase):
    @patch("nodepoint.services.document.django_rq.get_queue")
    def test_doc_preprocess_enqueues_existing_files(self, mock_get_queue):
        media_dir = tempfile.mkdtemp()
        with override_settings(MEDIA_ROOT=media_dir):
            workspace = Workspace.objects.create(name="preprocess-ws")
            document = Document.objects.create(
                workspace=workspace,
                file_name="note.md",
                file=SimpleUploadedFile("note.md", b"test content"),
            )
            filepath = document.file.path
            self.assertTrue(filepath)

            mock_queue = MagicMock()
            mock_get_queue.return_value = mock_queue

            count = doc_preprocess(workspace_name=workspace.name)
            self.assertEqual(count, 1)
            mock_queue.enqueue.assert_called_once()
            args = mock_queue.enqueue.call_args[0]
            self.assertEqual(args[1], document.id)
            self.assertEqual(args[2], filepath)


class AgentModelValidationTests(TestCase):
    @patch.dict("os.environ", {"BASE_URL": "https://api.example/v1", "API_KEY": "test-key"}, clear=False)
    @patch("nodepoint.agent.agent.Agent._request")
    def test_validate_model_skipped_when_env_set(self, mock_request):
        with patch.dict("os.environ", {"AGENT_SKIP_MODEL_VALIDATION": "true"}, clear=False):
            from nodepoint.agent.agent import Agent

            agent = Agent(settings_path="/nonexistent-settings.toml")
            agent.model = "GLM5"
            agent._validate_model("GLM5")
            mock_request.assert_not_called()

    @patch.dict("os.environ", {"BASE_URL": "https://api.example/v1", "API_KEY": "test-key"}, clear=False)
    @patch("nodepoint.agent.agent.Agent._request")
    def test_validate_model_allows_configured_model_when_catalog_fails(self, mock_request):
        from nodepoint.agent.agent import Agent

        mock_request.side_effect = RuntimeError("connection timed out")
        agent = Agent(settings_path="/nonexistent-settings.toml")
        agent.model = "GLM5"
        agent.vector_model = "embed-model"
        agent._validate_model("GLM5")
        self.assertEqual(mock_request.call_count, 1)


class PreprocessStatusAPITests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.workspace = Workspace.objects.create(name="status-ws")

    def test_workspace_not_found_returns_404(self):
        resp = self.client.get("/api/workspace/missing-ws/preprocess-status/")
        self.assertEqual(resp.status_code, 404)

    def test_empty_workspace_idle(self):
        resp = self.client.get("/api/workspace/status-ws/preprocess-status/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["workspace"], "status-ws")
        self.assertEqual(data["overall"]["phase"], "idle")
        self.assertTrue(data["overall"]["ready"])
        self.assertEqual(data["files"], [])

    def test_inprogress_document_processing_phase(self):
        Document.objects.create(
            workspace=self.workspace,
            file_name="a.md",
            status=Status.INPROGRESS,
        )
        resp = self.client.get("/api/workspace/status-ws/preprocess-status/")
        self.assertEqual(resp.json()["files"][0]["phase"], "processing")
        self.assertEqual(resp.json()["overall"]["phase"], "processing")

    def test_completed_with_pending_vectors_embedding(self):
        doc = Document.objects.create(
            workspace=self.workspace,
            file_name="b.md",
            status=Status.COMPLETED,
            content=True,
        )
        e1 = KnowledgeEntity.objects.create(
            document=doc, name="A", entity_type="PER", vector=Status.COMPLETED
        )
        KnowledgeEntity.objects.create(
            document=doc, name="B", entity_type="PER", vector=Status.PENDING
        )
        KnowledgeRelation.objects.create(
            document=doc,
            source=e1,
            target=e1,
            type_description="self",
            description="loop",
            vector=Status.PENDING,
        )
        resp = self.client.get("/api/workspace/status-ws/preprocess-status/")
        file_data = resp.json()["files"][0]
        self.assertEqual(file_data["phase"], "embedding")
        self.assertEqual(file_data["embedding_progress"], 0.3333)
        self.assertEqual(resp.json()["overall"]["phase"], "embedding")
        self.assertFalse(resp.json()["overall"]["ready"])

    def test_all_vectors_ready(self):
        doc = Document.objects.create(
            workspace=self.workspace,
            file_name="c.md",
            status=Status.COMPLETED,
            content=True,
        )
        e1 = KnowledgeEntity.objects.create(
            document=doc, name="X", entity_type="ORG", vector=Status.COMPLETED
        )
        KnowledgeRelation.objects.create(
            document=doc,
            source=e1,
            target=e1,
            type_description="self",
            description="loop",
            vector=Status.COMPLETED,
        )
        resp = self.client.get("/api/workspace/status-ws/preprocess-status/")
        self.assertEqual(resp.json()["files"][0]["phase"], "ready")
        self.assertEqual(resp.json()["files"][0]["embedding_progress"], 1.0)
        self.assertTrue(resp.json()["overall"]["ready"])
        self.assertEqual(resp.json()["overall"]["phase"], "ready")


from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync

from nodepoint.agent.schema import (
    AgentSessionDoneEvent,
    AssistantToolCallsMessageEvent,
    ChatCompletionFunction,
    ChatCompletionToolCallItem,
    ToolResultEvent,
)
from nodepoint.models import ChatMessage, ChatMessageRole
from nodepoint.services import chat_runner, chat_storage


class ChatStorageTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="chat-ws")
        self.conversation, self.root = chat_storage.create_conversation(self.workspace)

    def test_root_branch_created(self):
        self.assertTrue(self.root.is_root)
        self.assertEqual(self.root.conversation_id, self.conversation.id)

    def test_compression_branch_is_internal(self):
        child = chat_storage.create_branch_from_compression(
            self.conversation, self.root, "summary handoff"
        )
        self.assertTrue(child.is_internal)

    def test_active_branch_follows_compression_chain(self):
        child = chat_storage.create_branch_from_compression(
            self.conversation, self.root, "summary handoff"
        )
        active = chat_storage.get_active_branch(self.conversation.id)
        self.assertEqual(active.id, child.id)

    def test_child_branch_messages_not_in_root_load(self):
        child = chat_storage.create_branch_from_compression(
            self.conversation, self.root, "summary handoff"
        )
        chat_storage.append_message(
            child.id, role=ChatMessageRole.USER, content="branch-only"
        )
        root_messages = chat_storage.load_root_messages(self.conversation.id)
        contents = [m.content for m in root_messages]
        self.assertNotIn("branch-only", contents)

    def test_load_thread_round_trip(self):
        chat_storage.append_message(
            self.root.id, role=ChatMessageRole.USER, content="hello"
        )
        chat_storage.append_message(
            self.root.id,
            role=ChatMessageRole.ASSISTANT,
            content="hi",
            reasoning_content="thinking...",
        )
        thread, branch, conv = chat_storage.load_thread(self.root.id)
        self.assertEqual(branch.id, self.root.id)
        self.assertGreater(len(thread.messages), 0)

    def test_get_or_create_workspace_chat_is_idempotent(self):
        conv2, root2 = chat_storage.get_or_create_workspace_chat(self.workspace)
        self.assertEqual(conv2.id, self.conversation.id)
        self.assertEqual(root2.id, self.root.id)

    def test_clear_workspace_chat_resets_to_fresh_system_prompt(self):
        old_id = self.conversation.id
        chat_storage.append_message(
            self.root.id, role=ChatMessageRole.USER, content="hello"
        )
        conv, root = chat_storage.clear_workspace_chat(self.workspace)
        self.assertNotEqual(conv.id, old_id)
        fresh_messages = chat_storage.load_root_messages(conv.id)
        self.assertEqual(len(fresh_messages), 1)
        self.assertEqual(fresh_messages[0].role, ChatMessageRole.SYSTEM)


class ChatRunnerTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="chat-ws-runner")
        self.conversation, self.root = chat_storage.create_conversation(self.workspace)

    @patch("nodepoint.services.chat_runner.chat_compression.compress_async", new_callable=AsyncMock)
    @patch.object(chat_runner.Agent, "stream_agent_events_async")
    def test_runner_persists_tool_messages(self, mock_stream, mock_compress):
        tool_call = ChatCompletionToolCallItem(
            id="call_1",
            type="function",
            function=ChatCompletionFunction(name="search", arguments="{}"),
        )

        async def fake_stream(*args, **kwargs):
            yield AssistantToolCallsMessageEvent(
                tool_calls=[tool_call],
                content="",
                reasoning_content="reasoning text",
            )
            yield ToolResultEvent(
                tool_call_id="call_1",
                tool_name="search",
                result="ok",
                ok=True,
            )
            yield AgentSessionDoneEvent()

        mock_stream.return_value = fake_stream()
        mock_compress.return_value = "summary"

        thread, _, _ = chat_storage.load_thread(self.root.id)

        async def capture(payload):
            pass

        with patch.dict("os.environ", {"BASE_URL": "http://test", "API_KEY": "test-key"}):
            async_to_sync(chat_runner.run_agent_stream)(
                thread,
                chat_runner.Agent(),
                self.root.id,
                self.conversation.id,
                tools=[],
                on_event=capture,
            )

        roles = list(
            ChatMessage.objects.filter(branch=self.root).values_list("role", flat=True)
        )
        self.assertIn(ChatMessageRole.ASSISTANT, roles)
        self.assertIn(ChatMessageRole.TOOL, roles)


from rest_framework.test import APIClient

from nodepoint.services import kg_graph, kg_search
from nodepoint.services.chat_context import (
    reset_chat_workspace,
    resolve_search_workspace_names,
    set_chat_workspace,
)


class KgGraphServiceTests(TestCase):
    def setUp(self):
        self.flagged_ws = Workspace.objects.create(name="flagged-ws", is_flag=True)
        self.other_ws = Workspace.objects.create(name="other-ws", is_flag=False)
        self.doc_flagged = Document.objects.create(
            workspace=self.flagged_ws,
            file_name="a.md",
            file=SimpleUploadedFile("a.md", b"a"),
        )
        self.doc_other = Document.objects.create(
            workspace=self.other_ws,
            file_name="b.md",
            file=SimpleUploadedFile("b.md", b"b"),
        )
        graph = KnowledgeGraph(
            entities=[
                Entity(name="Alice", type="PER", attributes={"role": "eng"}),
                Entity(name="Bob", type="PER", attributes={}),
            ],
            relations=[
                Relation(
                    source="Alice",
                    target="Bob",
                    type_description="knows",
                    description="Alice knows Bob",
                ),
            ],
        )
        ingest_knowledge_graph(self.doc_flagged, graph)
        ingest_knowledge_graph(
            self.doc_other,
            KnowledgeGraph(
                entities=[Entity(name="Solo", type="PER", attributes={})],
                relations=[],
            ),
        )

    def test_build_workspace_graph_shape(self):
        graph = kg_graph.build_workspace_graph(self.flagged_ws)
        self.assertEqual(graph["workspace"], "flagged-ws")
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        node = graph["nodes"][0]
        self.assertIn("id", node)
        self.assertIn("name", node)
        self.assertIn("file_name", node)
        self.assertEqual(graph["edges"][0], {"source": "Alice", "target": "Bob"})

    def test_flagged_bulk_excludes_non_flagged(self):
        graphs = kg_graph.build_graphs_for_flagged_workspaces()
        names = [g["workspace"] for g in graphs]
        self.assertIn("flagged-ws", names)
        self.assertNotIn("other-ws", names)


class KnowledgeGraphAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ws = Workspace.objects.create(name="api-kg-ws", is_flag=True)
        self.doc = Document.objects.create(
            workspace=self.ws,
            file_name="doc.md",
            file=SimpleUploadedFile("doc.md", b"x"),
        )
        ingest_knowledge_graph(
            self.doc,
            KnowledgeGraph(
                entities=[Entity(name="N1", type="ORG", attributes={})],
                relations=[],
            ),
        )

    def test_get_by_workspace_name(self):
        resp = self.client.get(
            "/api/knowledge-graph/", {"workspace_name": "api-kg-ws"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["workspace"], "api-kg-ws")
        self.assertEqual(len(resp.json()["nodes"]), 1)

    def test_get_flagged(self):
        resp = self.client.get("/api/knowledge-graph/", {"flagged": "true"})
        self.assertEqual(resp.status_code, 200)
        workspaces = [g["workspace"] for g in resp.json()["graphs"]]
        self.assertIn("api-kg-ws", workspaces)

    def test_missing_params_returns_400(self):
        resp = self.client.get("/api/knowledge-graph/")
        self.assertEqual(resp.status_code, 400)


class WorkspaceChatAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ws = Workspace.objects.create(name="detail-ws")

    def test_get_lazy_creates_chat(self):
        resp = self.client.get("/api/chat/detail-ws/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["workspace"], "detail-ws")
        self.assertIn("messages", data)
        self.assertNotIn("branches", data)
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["role"], "system")

    def test_get_returns_root_messages_only(self):
        conversation, root = chat_storage.get_or_create_workspace_chat(self.ws)
        chat_storage.append_message(
            root.id, role=ChatMessageRole.USER, content="visible"
        )
        chat_storage.create_branch_from_compression(
            conversation, root, "hidden compression report"
        )
        resp = self.client.get("/api/chat/detail-ws/")
        self.assertEqual(resp.status_code, 200)
        contents = [m["content"] for m in resp.json()["messages"]]
        self.assertIn("visible", contents)
        self.assertNotIn("hidden compression report", contents)

    def test_delete_clears_chat(self):
        conversation, root = chat_storage.get_or_create_workspace_chat(self.ws)
        chat_storage.append_message(
            root.id, role=ChatMessageRole.USER, content="to clear"
        )
        del_resp = self.client.delete("/api/chat/detail-ws/")
        self.assertEqual(del_resp.status_code, 200)
        get_resp = self.client.get("/api/chat/detail-ws/")
        roles = [m["role"] for m in get_resp.json()["messages"]]
        self.assertEqual(roles, ["system"])
        self.assertFalse(
            Conversation.objects.filter(id=conversation.id).exists()
        )

    def test_unknown_workspace_returns_404(self):
        self.assertEqual(self.client.get("/api/chat/missing-ws/").status_code, 404)
        self.assertEqual(
            self.client.delete("/api/chat/missing-ws/").status_code, 404
        )


class ChatSummaryAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ws = Workspace.objects.create(name="summary-ws", is_flag=True)
        chat_storage.create_conversation(self.ws)

    def test_summary_by_workspace(self):
        resp = self.client.get("/api/chat/summary/", {"workspace_name": "summary-ws"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["workspace"], "summary-ws")
        self.assertIn("message_count", data)
        self.assertGreaterEqual(data["message_count"], 1)
        self.assertNotIn("conversations", data)

    def test_summary_flagged(self):
        resp = self.client.get("/api/chat/summary/", {"flagged": "true"})
        self.assertEqual(resp.status_code, 200)
        names = [w["workspace"] for w in resp.json()["workspaces"]]
        self.assertIn("summary-ws", names)


class ChatContextSearchScopeTests(TestCase):
    def test_resolve_includes_chat_workspace_and_flagged(self):
        Workspace.objects.create(name="chat-only", is_flag=False)
        Workspace.objects.create(name="flag-a", is_flag=True)
        Workspace.objects.create(name="flag-b", is_flag=True)

        token = set_chat_workspace("chat-only")
        try:
            names = resolve_search_workspace_names()
        finally:
            reset_chat_workspace(token)

        self.assertEqual(names, ["chat-only", "flag-a", "flag-b"])

    def test_flagged_scope_chat_searches_only_starred(self):
        from nodepoint.services.chat_context import (
            reset_flagged_scope_chat,
            set_flagged_scope_chat,
        )

        Workspace.objects.create(name="chat-only", is_flag=False)
        Workspace.objects.create(name="flag-a", is_flag=True)

        token = set_flagged_scope_chat(True)
        try:
            names = resolve_search_workspace_names()
        finally:
            reset_flagged_scope_chat(token)

        self.assertEqual(names, ["flag-a"])


class KgSearchTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="kg-search-ws")
        self.document = Document.objects.create(
            workspace=self.workspace,
            file_name="notes.md",
            file=SimpleUploadedFile("notes.md", b"# doc"),
        )
        graph = KnowledgeGraph(
            entities=[Entity(name="Alice", type="PER", attributes={"role": "eng"})],
            relations=[],
        )
        ingest_knowledge_graph(self.document, graph)

    def test_format_search_document_includes_source_header(self):
        entity = KnowledgeEntity.objects.get(document=self.document)
        doc = kg_search.format_search_document(
            [
                {
                    "kind": "entity",
                    "id": str(entity.id),
                    "score": 0.9,
                    "name": "Alice",
                    "entity_type": "PER",
                    "attributes": {"role": "eng"},
                    "workspace": "kg-search-ws",
                    "file_name": "notes.md",
                }
            ],
            "who is Alice",
        )
        self.assertIn("## [source: notes.md]", doc)
        self.assertIn("name: Alice", doc)

    def test_resolve_hits_loads_entity(self):
        entity = KnowledgeEntity.objects.get(document=self.document)
        records = kg_search.resolve_hits(
            [{"id": str(entity.id), "type": "entity", "score": 0.8, "payload": {}}]
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["file_name"], "notes.md")


class ChatSystemPromptTests(TestCase):
    def test_create_conversation_uses_fixed_system_prompt(self):
        from nodepoint.registry.prompt import Prompt

        ws = Workspace.objects.create(name="prompt-ws")
        conversation, _ = chat_storage.create_conversation(ws)
        self.assertEqual(conversation.system_prompt, Prompt["chat_system"])


class FlaggedScopeChatAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_flagged_chat_without_query_returns_400(self):
        self.assertEqual(self.client.get("/api/chat/").status_code, 400)

    def test_flagged_chat_works_without_user_starred_workspaces(self):
        resp = self.client.get("/api/chat/", {"flagged": "true"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["flagged"])
        self.assertEqual(data["starred_workspaces"], [])
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["role"], "system")

    def test_flagged_chat_separate_from_named_workspace(self):
        Workspace.objects.create(name="123", is_flag=True)
        chat_storage.get_or_create_workspace_chat(Workspace.objects.get(name="123"))
        flagged_resp = self.client.get("/api/chat/", {"flagged": "true"})
        ws_resp = self.client.get("/api/chat/123/")
        self.assertEqual(flagged_resp.status_code, 200)
        self.assertEqual(ws_resp.status_code, 200)
        self.assertTrue(flagged_resp.json()["flagged"])
        self.assertEqual(ws_resp.json()["workspace"], "123")

    def test_flagged_chat_lists_starred_workspaces(self):
        Workspace.objects.create(name="older", is_flag=True)
        Workspace.objects.create(name="newer", is_flag=True)
        resp = self.client.get("/api/chat/", {"flagged": "true"})
        self.assertEqual(resp.json()["starred_workspaces"], ["older", "newer"])


class UploadDefaultFlaggedTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch("nodepoint.views.document.django_rq.get_queue")
    def test_upload_without_workspace_uses_first_flagged(self, mock_get_queue):
        Workspace.objects.create(name="first-star", is_flag=True)
        Workspace.objects.create(name="second-star", is_flag=True)
        mock_get_queue.return_value = MagicMock()
        resp = self.client.post(
            "/api/document/upload/",
            {"file": SimpleUploadedFile("note.md", b"content")},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 200)
        from nodepoint.models import Document

        doc = Document.objects.get(id=resp.json()["id"])
        self.assertEqual(doc.workspace.name, "first-star")

    def test_upload_without_workspace_400_when_none_flagged(self):
        resp = self.client.post(
            "/api/document/upload/",
            {"file": SimpleUploadedFile("note.md", b"content")},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)


class CreateWorkspaceReservedNameTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_rejects_reserved_name_flagged(self):
        resp = self.client.post(
            "/api/workspace/create/",
            {"name": "flagged"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


class KnowledgeToolFlaggedScopeTests(TestCase):
    @patch("nodepoint.registry.tools.Knowledge.resolve_search_workspace_names")
    def test_search_graph_no_flagged_workspaces_message(self, mock_resolve):
        mock_resolve.return_value = []
        from nodepoint.registry.tools import Knowledge as knowledge_tools

        result = knowledge_tools.search_graph("query")
        self.assertIn("No workspace is flagged", result)


class ChatRunnerToolTests(TestCase):
    def test_default_tools_only_search_graph(self):
        from nodepoint.services import chat_runner

        names = {t["function"]["name"] for t in chat_runner.default_tools()}
        self.assertEqual(names, {"Knowledge.search_graph"})


class QdrantSearchTests(TestCase):
    @patch("nodepoint.quadrant.manager.client")
    def test_search_vector_uses_client_search(self, mock_client):
        from nodepoint.quadrant import manager

        mock_client.collection_exists.return_value = True
        mock_client.search.return_value = [
            type("Hit", (), {"id": "p1", "score": 0.9, "payload": {"type": "entity"}})()
        ]

        hits = manager.search_vector([0.1, 0.2], limit=5)
        mock_client.search.assert_called_once()
        call_kwargs = mock_client.search.call_args.kwargs
        self.assertEqual(call_kwargs["query_vector"], [0.1, 0.2])
        self.assertEqual(call_kwargs["limit"], 5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].id, "p1")


class ToolInvokeAsyncTests(TestCase):
    @patch("nodepoint.registry.tools.Knowledge.kg_search.build_search_document")
    @patch("nodepoint.registry.tools.Knowledge.search_by_workspaces")
    @patch("nodepoint.registry.tools.Knowledge.resolve_search_workspace_names")
    @patch("nodepoint.registry.tools.Knowledge.get_agent")
    def test_invoke_async_runs_local_tool_off_event_loop(
        self, mock_get_agent, mock_resolve, mock_search, mock_build
    ):
        from nodepoint.agent.agent import ToolCallNormalized
        from nodepoint.registry.tool import Tool

        mock_resolve.return_value = ["global"]
        mock_get_agent.return_value.vector.return_value.squeeze.return_value.tolist.return_value = [
            0.1,
        ]
        mock_search.return_value = []
        mock_build.return_value = "ok"

        call = ToolCallNormalized(
            name="Knowledge.search_graph",
            args={"query": "nmap result", "limit": 10},
            id="call_1",
        )

        async def run():
            return await Tool.invoke_async(call)

        result = async_to_sync(run)()
        self.assertEqual(result, "ok")
        mock_build.assert_called_once()


class KnowledgeToolTests(TestCase):
    @patch("nodepoint.registry.tools.Knowledge.kg_search.build_search_document")
    @patch("nodepoint.registry.tools.Knowledge.search_by_workspaces")
    @patch("nodepoint.registry.tools.Knowledge.resolve_search_workspace_names")
    @patch("nodepoint.registry.tools.Knowledge.get_agent")
    def test_search_graph_returns_markdown_document(
        self, mock_get_agent, mock_resolve, mock_search, mock_build
    ):
        mock_resolve.return_value = ["chat-ws"]
        mock_get_agent.return_value.vector.return_value.squeeze.return_value.tolist.return_value = [
            0.1,
            0.2,
        ]
        mock_search.return_value = [{"id": "1", "score": 0.9, "type": "entity"}]
        mock_build.return_value = '## [source: notes.md]\n**Entity**\n'

        from nodepoint.registry.tools import Knowledge as knowledge_tools

        result = knowledge_tools.search_graph("query", limit=5)
        self.assertIn("[source: notes.md]", result)
        mock_build.assert_called_once()

    @patch("nodepoint.registry.tools.Knowledge.resolve_search_workspace_names")
    def test_search_graph_empty_when_no_workspaces(self, mock_resolve):
        mock_resolve.return_value = []
        from nodepoint.registry.tools import Knowledge as knowledge_tools

        result = knowledge_tools.search_graph("query")
        self.assertIn("No workspace is flagged", result)
