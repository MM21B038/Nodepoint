import tempfile
import uuid
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings

from nodepoint.backend.kg_builder import ingest_knowledge_graph
from nodepoint.enums import Status
from nodepoint.models import (
    ChatBranch,
    Conversation,
    Document,
    DocumentChunk,
    KnowledgeEntity,
    KnowledgeRelation,
    Workspace,
)
from nodepoint.backend.kg_builder import ingest_knowledge_graph_for_chunk
from nodepoint.registry.schema import Entity, Relation
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
        entities=[
            Entity(name="Alice", type="PER", attributes={}),
            Entity(name="Acme", type="ORG", attributes={}),
        ]
        relations=[
            Relation(
                source="Alice",
                target="Acme",
                type_description="works at",
                description="Alice works at Acme Corp.",
            ),
        ]

        ok, entity_ids, relation_ids = ingest_knowledge_graph(self.document, entities, relations)
        self.assertTrue(ok)
        self.assertEqual(len(entity_ids), 2)
        self.assertEqual(len(relation_ids), 1)

        relation = KnowledgeRelation.objects.get(id=relation_ids[0])
        self.assertEqual(relation.source.name, "Alice")
        self.assertEqual(relation.target.name, "Acme")

    def test_ingest_is_idempotent(self):
        entities=[Entity(name="Node", type="TECH", attributes={})]
        relations=[]
        ingest_knowledge_graph(self.document, entities, relations)
        ingest_knowledge_graph(self.document, entities, relations)
        self.assertEqual(
            KnowledgeEntity.objects.filter(document=self.document).count(),
            1,
        )


class PreprocessPipelineTests(TestCase):
    @patch("nodepoint.services.preprocess_pipeline._orchestrator_queue")
    def test_enqueue_pipeline_with_upload_sequential_order(self, mock_get_queue):
        from nodepoint.services.preprocess_pipeline import enqueue_preprocess_pipeline

        mock_queue = MagicMock()
        mock_get_queue.return_value = mock_queue
        j1, j2, j3 = MagicMock(), MagicMock(), MagicMock()
        j1.id, j2.id, j3.id = "j1", "j2", "j3"
        mock_queue.enqueue.side_effect = [j1, j2, j3]

        doc_id = uuid.uuid4()
        result = enqueue_preprocess_pipeline(
            uploaded_document_id=doc_id,
            workspace_name="upload-ws",
        )

        self.assertEqual(mock_queue.enqueue.call_count, 3)
        self.assertEqual(len(result["steps"]), 3)
        self.assertIn("document-scoped", result["message"])

        calls = mock_queue.enqueue.call_args_list
        self.assertEqual(calls[0][0][0].__name__, "run_prepare_document")
        self.assertEqual(calls[0][0][1], doc_id)
        self.assertEqual(calls[1][0][0].__name__, "run_chunk_preprocess_batch")
        self.assertEqual(calls[1][0][1], "upload-ws")
        self.assertEqual(calls[1][1]["document_ids"], [doc_id])
        self.assertEqual(calls[1][1]["depends_on"], [j1])
        self.assertEqual(calls[2][0][0].__name__, "schedule_workspace_pipeline_tail")
        self.assertEqual(calls[2][0][1], "upload-ws")
        self.assertEqual(calls[2][1]["depends_on"], [j2])

    @patch("nodepoint.services.preprocess_pipeline.try_acquire_workspace_pipeline_lock")
    @patch("nodepoint.services.preprocess_pipeline._orchestrator_queue")
    def test_enqueue_workspace_pipeline_coalesces_when_lock_held(
        self, mock_get_queue, mock_acquire
    ):
        from nodepoint.services.preprocess_pipeline import enqueue_preprocess_pipeline

        mock_acquire.return_value = False
        result = enqueue_preprocess_pipeline(workspace_name="my-ws")

        mock_get_queue.assert_not_called()
        self.assertTrue(result["coalesced"])
        self.assertEqual(result["steps"], [])
        self.assertIn("already queued", result["message"])

    @patch("nodepoint.services.preprocess_pipeline.try_acquire_workspace_pipeline_lock")
    @patch("nodepoint.services.preprocess_pipeline._orchestrator_queue")
    def test_enqueue_pipeline_post_prepare_legacy_then_sequential(
        self, mock_get_queue, mock_acquire
    ):
        from nodepoint.services.preprocess_pipeline import enqueue_preprocess_pipeline

        mock_acquire.return_value = True
        mock_queue = MagicMock()
        mock_get_queue.return_value = mock_queue
        j1, j2, j3, j4 = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        j1.id, j2.id, j3.id, j4.id = "j1", "j2", "j3", "j4"
        mock_queue.enqueue.side_effect = [j1, j2, j3, j4]

        result = enqueue_preprocess_pipeline(workspace_name="my-ws")

        self.assertEqual(mock_queue.enqueue.call_count, 4)
        self.assertIn("prepare legacy", result["message"])
        self.assertIn("my-ws", result["message"])
        calls = mock_queue.enqueue.call_args_list
        self.assertEqual(calls[0][0][0].__name__, "run_prepare_legacy_batch")
        self.assertEqual(calls[0][0][1], "my-ws")
        self.assertEqual(calls[1][1]["depends_on"], [j1])
        self.assertEqual(calls[2][1]["depends_on"], [j2])
        self.assertEqual(calls[3][1]["depends_on"], [j3])

    @patch("nodepoint.services.preprocess_pipeline.try_acquire_workspace_pipeline_lock")
    @patch("nodepoint.services.preprocess_pipeline._orchestrator_queue")
    def test_schedule_workspace_pipeline_tail_coalesces(self, mock_get_queue, mock_acquire):
        from nodepoint.services.preprocess_pipeline import schedule_workspace_pipeline_tail

        mock_acquire.return_value = False
        result = schedule_workspace_pipeline_tail("busy-ws")
        self.assertTrue(result["coalesced"])
        mock_get_queue.assert_not_called()

    @patch("nodepoint.services.preprocess_pipeline.enqueue_chunks_for_documents", return_value=2)
    def test_run_chunk_preprocess_batch_non_blocking(self, mock_enqueue):
        from nodepoint.services.preprocess_pipeline import run_chunk_preprocess_batch

        doc_id = uuid.uuid4()
        count = run_chunk_preprocess_batch(
            workspace_name="ws",
            document_ids=[doc_id],
        )
        self.assertEqual(count, 2)
        mock_enqueue.assert_called_once_with(
            document_ids=[doc_id],
            workspace_name="ws",
            wait=False,
        )

    @patch("nodepoint.views.preprocess.enqueue_priority_workspace_preprocess")
    def test_post_preprocess_defaults_priority_and_others(self, mock_enqueue):
        from rest_framework.test import APIClient

        mock_enqueue.return_value = {
            "message": "ok",
            "priority_workspace": "post-ws",
            "priority_pipeline": {"steps": [], "jobs": {}},
            "other_workspaces": [],
        }
        client = APIClient()
        Workspace.objects.create(name="post-ws")
        resp = client.post("/api/workspace/preprocess/post-ws/")
        self.assertEqual(resp.status_code, 200)
        mock_enqueue.assert_called_once_with(
            "post-ws",
            priority=True,
            include_other_workspaces=True,
        )
        self.assertEqual(resp.data["priority_workspace"], "post-ws")

    @patch("nodepoint.views.preprocess.enqueue_priority_workspace_preprocess")
    def test_post_preprocess_query_params(self, mock_enqueue):
        from rest_framework.test import APIClient

        mock_enqueue.return_value = {
            "message": "ok",
            "priority_workspace": "post-ws",
            "priority_pipeline": {},
            "other_workspaces": [],
        }
        client = APIClient()
        Workspace.objects.create(name="post-ws")
        resp = client.post(
            "/api/workspace/preprocess/post-ws/"
            "?priority=false&include_other_workspaces=false"
        )
        self.assertEqual(resp.status_code, 200)
        mock_enqueue.assert_called_once_with(
            "post-ws",
            priority=False,
            include_other_workspaces=False,
        )

    @patch("nodepoint.services.preprocess_pipeline._enqueue_workspace_preprocess")
    @patch("nodepoint.services.preprocess_pipeline._other_workspaces_needing_preprocess")
    def test_enqueue_priority_routes_queues(
        self, mock_others, mock_enqueue
    ):
        from nodepoint.services.preprocess_pipeline import (
            enqueue_priority_workspace_preprocess,
        )

        mock_enqueue.side_effect = [
            {"coalesced": False, "jobs": {"prepare_legacy": "j1"}},
            {"coalesced": True, "jobs": {}},
        ]
        mock_others.return_value = ["ws-b"]

        result = enqueue_priority_workspace_preprocess(
            "ws-a",
            priority=True,
            include_other_workspaces=True,
        )

        self.assertEqual(result["priority_workspace"], "ws-a")
        self.assertEqual(len(result["other_workspaces"]), 1)
        self.assertFalse(result["other_workspaces"][0]["queued"])
        self.assertTrue(result["other_workspaces"][0]["coalesced"])
        self.assertEqual(mock_enqueue.call_count, 2)
        mock_enqueue.assert_any_call(
            "ws-a", orchestrator_queue_name="high"
        )
        mock_enqueue.assert_any_call(
            "ws-b", orchestrator_queue_name="low"
        )

    @patch("nodepoint.services.preprocess_pipeline._enqueue_workspace_preprocess")
    def test_enqueue_priority_single_workspace_no_others(self, mock_enqueue):
        from nodepoint.services.preprocess_pipeline import (
            enqueue_priority_workspace_preprocess,
        )

        mock_enqueue.return_value = {"coalesced": False, "jobs": {}}

        enqueue_priority_workspace_preprocess(
            "ws-a",
            priority=False,
            include_other_workspaces=False,
        )

        mock_enqueue.assert_called_once_with(
            "ws-a", orchestrator_queue_name="orchestrator"
        )

    @patch("nodepoint.services.chunking.django_rq.get_queue")
    def test_enqueue_chunks_includes_inprogress_documents(self, mock_get_queue):
        from nodepoint.services.chunking import enqueue_chunks_for_documents

        workspace = Workspace.objects.create(name="inprogress-ws")
        document = Document.objects.create(
            workspace=workspace,
            file_name="note.md",
            status=Status.INPROGRESS,
            content=True,
        )
        chunk = DocumentChunk.objects.create(
            document=document,
            index=0,
            status=Status.PENDING,
            vector=Status.PENDING,
        )
        mock_queue = MagicMock()
        mock_get_queue.return_value = mock_queue

        count = enqueue_chunks_for_documents(workspace_name=workspace.name)
        self.assertEqual(count, 1)
        mock_queue.enqueue.assert_called_once()
        chunk.refresh_from_db()
        self.assertEqual(chunk.status, Status.QUEUED)

    @patch("nodepoint.services.document.enqueue_chunks_for_document", return_value=1)
    @patch("nodepoint.services.document.prepare_document", return_value=[uuid.uuid4()])
    def test_process_doc_prepares_and_enqueues_chunks(self, mock_prepare, mock_enqueue):
        import tempfile

        from nodepoint.services.document import process_doc

        media_dir = tempfile.mkdtemp()
        with override_settings(MEDIA_ROOT=media_dir):
            workspace = Workspace.objects.create(name="pipeline-ws")
            document = Document.objects.create(
                workspace=workspace,
                file_name="note.md",
                file=SimpleUploadedFile("note.md", b"hello world"),
            )
            process_doc(document.id, document.file.path)

        mock_prepare.assert_called_once()
        mock_enqueue.assert_called_once_with(document.id)

    @patch("nodepoint.services.preprocess_pipeline.ingest_chunk", return_value=True)
    @patch("nodepoint.services.preprocess_pipeline.read_document_content", return_value="fixed text")
    @patch("nodepoint.backend.kg_builder.split_doc", return_value=["fixed text"])
    def test_chunk_mongo_repair_batch(self, mock_split, mock_read, mock_ingest):
        from nodepoint.services.preprocess_pipeline import run_chunk_mongo_repair_batch

        workspace = Workspace.objects.create(name="repair-ws")
        document = Document.objects.create(
            workspace=workspace,
            file_name="broken.md",
            file=SimpleUploadedFile("broken.md", b"content"),
            content=False,
            status=Status.COMPLETED,
        )

        count = run_chunk_mongo_repair_batch()
        self.assertEqual(count, 1)
        document.refresh_from_db()
        self.assertTrue(document.content)
        self.assertEqual(DocumentChunk.objects.filter(document=document).count(), 1)
        mock_ingest.assert_called()


class ChunkPipelineTests(TestCase):
    @patch("nodepoint.services.chunking.ingest_chunk", return_value=True)
    @patch("nodepoint.services.chunking.split_doc", return_value=["part one", "part two"])
    def test_prepare_document_creates_chunks(self, mock_split, mock_ingest):
        import tempfile

        from nodepoint.services.chunking import prepare_document

        media_dir = tempfile.mkdtemp()
        with override_settings(MEDIA_ROOT=media_dir):
            workspace = Workspace.objects.create(name="chunk-ws")
            document = Document.objects.create(
                workspace=workspace,
                file_name="note.md",
                file=SimpleUploadedFile("note.md", b"hello world"),
            )
            chunk_ids = prepare_document(document.id, document.file.path)

        self.assertEqual(len(chunk_ids), 2)
        self.assertEqual(DocumentChunk.objects.filter(document=document).count(), 2)
        document.refresh_from_db()
        self.assertTrue(document.content)

    def test_ingest_for_chunk_does_not_delete_other_chunks(self):
        workspace = Workspace.objects.create(name="kg-chunk-ws")
        document = Document.objects.create(
            workspace=workspace,
            file_name="a.md",
            file=SimpleUploadedFile("a.md", b"x"),
        )
        chunk_a = DocumentChunk.objects.create(document=document, index=0, status=Status.COMPLETED)
        chunk_b = DocumentChunk.objects.create(document=document, index=1, status=Status.PENDING)
        KnowledgeEntity.objects.create(
            document=document, chunk=chunk_a, name="Keep", entity_type="PER"
        )
        entities = [Entity(name="New", type="ORG", attributes={})]
        ok, _, _ = ingest_knowledge_graph_for_chunk(document, chunk_b, entities, [])
        self.assertTrue(ok)
        self.assertEqual(KnowledgeEntity.objects.filter(chunk=chunk_a).count(), 1)
        self.assertEqual(KnowledgeEntity.objects.filter(chunk=chunk_b).count(), 1)

    def test_rollup_document_completed_when_all_chunks_done(self):
        from nodepoint.services.chunking import rollup_document_status

        workspace = Workspace.objects.create(name="rollup-ws")
        document = Document.objects.create(
            workspace=workspace,
            file_name="b.md",
            file=SimpleUploadedFile("b.md", b"y"),
            status=Status.INPROGRESS,
        )
        DocumentChunk.objects.create(document=document, index=0, status=Status.COMPLETED)
        DocumentChunk.objects.create(document=document, index=1, status=Status.COMPLETED)
        rollup_document_status(document.id)
        document.refresh_from_db()
        self.assertEqual(document.status, Status.COMPLETED)

    @patch("nodepoint.services.chunking.time.sleep")
    @patch("nodepoint.services.chunking.time.monotonic")
    def test_wait_for_chunk_job_polls_until_finished(self, mock_monotonic, mock_sleep):
        from nodepoint.services.chunking import _wait_for_chunk_job
        from rq.job import JobStatus

        mock_job = MagicMock()
        mock_job.id = "job-1"
        mock_job.get_status.side_effect = [
            JobStatus.QUEUED,
            JobStatus.STARTED,
            JobStatus.FINISHED,
        ]
        mock_monotonic.side_effect = [0.0, 0.1, 0.2]

        _wait_for_chunk_job(mock_job, timeout_seconds=300)

        self.assertEqual(mock_job.get_status.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("nodepoint.services.chunking.time.sleep")
    @patch("nodepoint.services.chunking.time.monotonic")
    def test_wait_for_chunk_jobs_uses_batch_deadline(self, mock_monotonic, mock_sleep):
        from nodepoint.services.chunking import wait_for_chunk_jobs
        from rq.job import JobStatus

        job_a = MagicMock()
        job_a.id = "job-a"
        job_b = MagicMock()
        job_b.id = "job-b"
        job_a.get_status.side_effect = [JobStatus.STARTED, JobStatus.FINISHED]
        job_b.get_status.side_effect = [JobStatus.QUEUED, JobStatus.FINISHED]
        mock_monotonic.side_effect = [0.0, 0.1, 0.2, 0.3]

        with patch(
            "nodepoint.services.chunking.CHUNK_BATCH_WAIT_SECONDS",
            600,
        ):
            wait_for_chunk_jobs([job_a, job_b])

        self.assertGreaterEqual(job_a.get_status.call_count, 1)
        self.assertGreaterEqual(job_b.get_status.call_count, 1)

    @patch("nodepoint.services.chunking.wait_for_chunk_jobs")
    @patch("nodepoint.services.chunking.django_rq.get_queue")
    def test_enqueue_chunks_can_wait_for_jobs(self, mock_get_queue, mock_wait):
        from nodepoint.services.chunking import enqueue_chunks_for_documents

        workspace = Workspace.objects.create(name="wait-ws")
        document = Document.objects.create(
            workspace=workspace,
            file_name="w.md",
            file=SimpleUploadedFile("w.md", b"x"),
            status=Status.QUEUED,
            content=True,
        )
        DocumentChunk.objects.create(document=document, index=0, status=Status.PENDING)

        mock_job = MagicMock()
        mock_get_queue.return_value.enqueue.return_value = mock_job

        count = enqueue_chunks_for_documents(
            document_ids=[document.id],
            wait=True,
        )
        self.assertEqual(count, 1)
        mock_wait.assert_called_once_with([mock_job])

    def test_rollup_legacy_completed_not_downgraded(self):
        from nodepoint.services.chunking import rollup_document_status

        workspace = Workspace.objects.create(name="legacy-rollup-ws")
        document = Document.objects.create(
            workspace=workspace,
            file_name="legacy.md",
            file=SimpleUploadedFile("legacy.md", b"x"),
            status=Status.COMPLETED,
            content=True,
        )
        KnowledgeEntity.objects.create(
            document=document, name="Legacy", entity_type="PER"
        )
        rollup_document_status(document.id)
        document.refresh_from_db()
        self.assertEqual(document.status, Status.COMPLETED)

    @patch("nodepoint.services.chunking.ingest_chunk", return_value=True)
    @patch("nodepoint.services.chunking.split_doc", return_value=["chunk one"])
    def test_run_prepare_legacy_batch_creates_chunks(self, mock_split, mock_ingest):
        import tempfile

        from nodepoint.services.chunking import run_prepare_legacy_batch

        media_dir = tempfile.mkdtemp()
        with override_settings(MEDIA_ROOT=media_dir):
            workspace = Workspace.objects.create(name="legacy-prepare-ws")
            document = Document.objects.create(
                workspace=workspace,
                file_name="old.md",
                file=SimpleUploadedFile("old.md", b"legacy body"),
                status=Status.COMPLETED,
                content=True,
            )
            count = run_prepare_legacy_batch(workspace_name=workspace.name)

        self.assertEqual(count, 1)
        self.assertEqual(DocumentChunk.objects.filter(document=document).count(), 1)


class DocPreprocessTests(TestCase):
    @patch("nodepoint.services.chunking.django_rq.get_queue")
    @patch("nodepoint.services.chunking.ingest_chunk", return_value=True)
    @patch("nodepoint.services.chunking.split_doc", return_value=["chunk text"])
    def test_doc_preprocess_enqueues_chunk_jobs(self, mock_split, mock_ingest, mock_get_queue):
        media_dir = tempfile.mkdtemp()
        with override_settings(MEDIA_ROOT=media_dir):
            workspace = Workspace.objects.create(name="preprocess-ws")
            document = Document.objects.create(
                workspace=workspace,
                file_name="note.md",
                file=SimpleUploadedFile("note.md", b"test content"),
                status=Status.PENDING,
            )

            mock_queue = MagicMock()
            mock_get_queue.return_value = mock_queue

            count = doc_preprocess(workspace_name=workspace.name)
            self.assertGreaterEqual(count, 1)
            mock_queue.enqueue.assert_called()
            args = mock_queue.enqueue.call_args[0]
            from nodepoint.services.chunk_process import process_chunk

            self.assertEqual(args[0], process_chunk)
        mock_get_queue.assert_called_with("chunk")


class PerChunkVectorEnqueueTests(TestCase):
    @patch("nodepoint.services.vector._vector_queue")
    def test_enqueue_vectors_for_chunk(self, mock_get_queue):
        from nodepoint.services.vector import enqueue_vectors_for_chunk

        mock_queue = MagicMock()
        mock_get_queue.return_value = mock_queue
        chunk_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        relation_id = uuid.uuid4()

        count = enqueue_vectors_for_chunk(
            chunk_id,
            entity_ids=[entity_id],
            relation_ids=[relation_id],
        )
        self.assertEqual(count, 3)
        self.assertEqual(mock_queue.enqueue.call_count, 3)

    @patch("nodepoint.services.vector.enqueue_vectors_for_chunk")
    @patch("nodepoint.services.chunk_process.ingest_knowledge_graph_for_chunk")
    @patch("nodepoint.services.chunk_process.get_chunk_text", return_value="chunk body")
    @patch("nodepoint.services.chunk_process.extract_knowledge_graph")
    @patch("nodepoint.services.chunk_process.Agent")
    def test_process_chunk_enqueues_vectors_per_chunk(
        self,
        mock_agent_cls,
        mock_extract,
        mock_get_text,
        mock_ingest,
        mock_enqueue_vectors,
    ):
        from nodepoint.services.chunk_process import process_chunk

        mock_agent = MagicMock()
        mock_agent_cls.return_value = mock_agent
        entity = Entity(name="Alice", type="PER", attributes={})
        mock_extract.return_value = ([entity], [])
        entity_id = uuid.uuid4()
        mock_ingest.return_value = (True, [entity_id], [])
        workspace = Workspace.objects.create(name="vec-ws")
        document = Document.objects.create(
            workspace=workspace,
            file_name="v.md",
            file=SimpleUploadedFile("v.md", b"x"),
            status=Status.QUEUED,
            content=True,
        )
        chunk = DocumentChunk.objects.create(
            document=document,
            index=0,
            status=Status.QUEUED,
        )

        process_chunk(chunk.id)

        mock_enqueue_vectors.assert_called_once_with(chunk.id, [entity_id], [])
        chunk.refresh_from_db()
        self.assertEqual(chunk.status, Status.COMPLETED)


class AgentParserModelTests(TestCase):
    @patch.dict(
        "os.environ",
        {"BASE_URL": "https://api.example/v1", "API_KEY": "test-key"},
        clear=False,
    )
    def test_parser_model_from_toml(self):
        import tempfile
        from pathlib import Path

        from nodepoint.agent.agent import Agent

        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as f:
            f.write(
                b'[agent]\nmodel = "chat-model"\nparser = "parse-model"\nvector = "embed"\n'
            )
            path = Path(f.name)

        agent = Agent(settings_path=path)
        self.assertEqual(agent.model, "chat-model")
        self.assertEqual(agent.parser_model, "parse-model")
        self.assertEqual(agent._resolve_model(None, agent.parser_model), "parse-model")

    @patch.dict(
        "os.environ",
        {
            "BASE_URL": "https://api.example/v1",
            "API_KEY": "test-key",
            "AGENT_PARSER": "env-parser",
        },
        clear=False,
    )
    def test_parser_model_env_overrides_toml(self):
        from nodepoint.agent.agent import Agent

        agent = Agent(settings_path="/nonexistent-settings.toml")
        agent.model = "chat-model"
        self.assertEqual(agent.parser_model, "env-parser")


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
        DocumentChunk.objects.create(
            document=doc, index=0, status=Status.COMPLETED, vector=Status.PENDING
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
        self.assertEqual(file_data["embedding_progress"], 0.25)
        self.assertEqual(resp.json()["overall"]["phase"], "embedding")
        self.assertFalse(resp.json()["overall"]["ready"])

    def test_all_vectors_ready(self):
        doc = Document.objects.create(
            workspace=self.workspace,
            file_name="c.md",
            status=Status.COMPLETED,
            content=True,
        )
        DocumentChunk.objects.create(
            document=doc, index=0, status=Status.COMPLETED, vector=Status.COMPLETED
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

    def test_legacy_completed_zero_chunks_embedding_not_queued(self):
        doc = Document.objects.create(
            workspace=self.workspace,
            file_name="legacy.md",
            status=Status.COMPLETED,
            content=True,
        )
        KnowledgeEntity.objects.create(
            document=doc, name="A", entity_type="PER", vector=Status.PENDING
        )
        resp = self.client.get("/api/workspace/status-ws/preprocess-status/")
        file_data = resp.json()["files"][0]
        self.assertEqual(file_data["phase"], "embedding")
        self.assertTrue(file_data["legacy"])
        self.assertNotEqual(file_data["phase"], "queued")

    def test_legacy_completed_all_vectors_ready(self):
        doc = Document.objects.create(
            workspace=self.workspace,
            file_name="legacy-ready.md",
            status=Status.COMPLETED,
            content=True,
        )
        KnowledgeEntity.objects.create(
            document=doc, name="X", entity_type="ORG", vector=Status.COMPLETED
        )
        resp = self.client.get("/api/workspace/status-ws/preprocess-status/")
        self.assertEqual(resp.json()["files"][0]["phase"], "ready")

    def test_zero_chunks_no_content_needs_prepare(self):
        Document.objects.create(
            workspace=self.workspace,
            file_name="unprepared.md",
            status=Status.COMPLETED,
            content=False,
        )
        resp = self.client.get("/api/workspace/status-ws/preprocess-status/")
        self.assertEqual(resp.json()["files"][0]["phase"], "needs_prepare")


class LegacyDerivePhaseTests(TestCase):
    def test_completed_legacy_with_pending_vectors_is_embedding(self):
        from nodepoint.services.preprocess_status import derive_file_phase

        chunks = {"total": 0, "pending": 0, "queued": 0, "in_progress": 0, "completed": 0, "failed": 0}
        entities = {"total": 1, "pending": 1, "completed": 0, "failed": 0}
        relations = {"total": 0, "pending": 0, "completed": 0, "failed": 0}
        chunk_vectors = {"total": 0, "pending": 0, "completed": 0, "failed": 0}
        phase = derive_file_phase(
            Status.COMPLETED,
            chunks,
            entities,
            relations,
            chunk_vectors,
            content=True,
        )
        self.assertEqual(phase, "embedding")

    def test_completed_legacy_no_kg_needs_prepare(self):
        from nodepoint.services.preprocess_status import derive_file_phase

        empty = {"total": 0, "pending": 0, "completed": 0, "failed": 0}
        phase = derive_file_phase(
            Status.COMPLETED,
            {**empty, "queued": 0, "in_progress": 0},
            empty,
            empty,
            empty,
            content=True,
        )
        self.assertEqual(phase, "needs_prepare")


class QueueStatusServiceTests(TestCase):
    def test_parse_args_summary_workspace_batch(self):
        from nodepoint.services.queue_status import _parse_args_summary

        summary = _parse_args_summary(
            "nodepoint.services.preprocess_pipeline.run_chunk_preprocess_batch",
            ("my-ws",),
            {"document_ids": [uuid.uuid4()]},
        )
        self.assertEqual(summary["workspace"], "my-ws")
        self.assertIn("document_ids", summary)

    def test_parse_args_summary_prepare_document(self):
        from nodepoint.services.queue_status import _parse_args_summary

        doc_id = uuid.uuid4()
        summary = _parse_args_summary(
            "nodepoint.services.preprocess_pipeline.run_prepare_document",
            (doc_id,),
            {},
        )
        self.assertEqual(summary["document_id"], str(doc_id))

    @patch("nodepoint.services.queue_status.build_active_pipelines")
    @patch("nodepoint.services.queue_status.build_rq_snapshot")
    @patch("nodepoint.services.queue_status._scan_pipeline_locks")
    def test_build_queue_status_structure(self, mock_locks, mock_rq, mock_active):
        from nodepoint.services.queue_status import build_queue_status

        mock_locks.return_value = [
            {"workspace": "ws-a", "key": "nodepoint:preprocess:pipeline:ws-a", "ttl_seconds": 3600}
        ]
        mock_rq.return_value = {"queues": {}, "workers": []}
        mock_active.return_value = []

        data = build_queue_status()
        self.assertIn("generated_at", data)
        self.assertIsNone(data["workspace_filter"])
        self.assertEqual(data["redis"]["pipeline_locks"][0]["workspace"], "ws-a")
        self.assertIn("database", data)
        self.assertIn("active_pipelines", data)

    def test_database_backlog_counts(self):
        from nodepoint.services.queue_status import build_database_backlog

        ws = Workspace.objects.create(name="queue-ws")
        doc = Document.objects.create(
            workspace=ws, file_name="q.md", status=Status.INPROGRESS
        )
        DocumentChunk.objects.create(
            document=doc, index=0, status=Status.QUEUED, vector=Status.PENDING
        )
        KnowledgeEntity.objects.create(
            document=doc, name="E", entity_type="PER", vector=Status.PENDING
        )

        backlog = build_database_backlog(workspace="queue-ws")
        self.assertEqual(backlog["documents"]["INPROGRESS"], 1)
        self.assertEqual(backlog["chunks"]["QUEUED"], 1)
        self.assertEqual(backlog["vectors"]["entities"]["pending"], 1)


class QueueStatusAPITests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()

    @patch("nodepoint.views.preprocess.build_queue_status")
    def test_queue_status_endpoint(self, mock_build):
        mock_build.return_value = {
            "generated_at": "2026-05-29T00:00:00+00:00",
            "workspace_filter": None,
            "rq": {"queues": {}, "workers": []},
            "redis": {"pipeline_locks": []},
            "database": {},
            "active_pipelines": [],
        }
        resp = self.client.get("/api/preprocess/queue-status/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["generated_at"], "2026-05-29T00:00:00+00:00")
        mock_build.assert_called_once_with(workspace=None)

    @patch("nodepoint.views.preprocess.build_queue_status")
    def test_queue_status_workspace_query(self, mock_build):
        mock_build.return_value = {"workspace_filter": "filtered-ws"}
        resp = self.client.get("/api/preprocess/queue-status/?workspace=filtered-ws")
        self.assertEqual(resp.status_code, 200)
        mock_build.assert_called_once_with(workspace="filtered-ws")

    @patch("nodepoint.services.queue_status.Worker")
    @patch("nodepoint.services.queue_status.django_rq.get_queue")
    @patch("nodepoint.services.queue_status.get_connection")
    def test_rq_snapshot_mocked(self, mock_conn, mock_get_queue, mock_worker_cls):
        from nodepoint.services.queue_status import build_rq_snapshot

        mock_connection = MagicMock()
        mock_conn.return_value = mock_connection

        mock_queue = MagicMock()
        mock_queue.count = 2
        mock_queue.get_jobs.return_value = []
        mock_get_queue.return_value = mock_queue

        with patch("nodepoint.services.queue_status.StartedJobRegistry") as mock_started, patch(
            "nodepoint.services.queue_status.FailedJobRegistry"
        ) as mock_failed, patch(
            "nodepoint.services.queue_status.DeferredJobRegistry"
        ) as mock_deferred:
            for reg in (mock_started, mock_failed, mock_deferred):
                reg.return_value.count = 0
                reg.return_value.get_job_ids.return_value = []

            mock_worker_cls.all.return_value = []

            snapshot = build_rq_snapshot()
            self.assertIn("orchestrator", snapshot["queues"])
            self.assertEqual(snapshot["queues"]["orchestrator"]["counts"]["queued"], 2)


import asyncio

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

        mock_stream.assert_called_once()
        self.assertFalse(mock_stream.call_args.kwargs.get("register_mcp_tools", True))

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
        from nodepoint.services import workspace_group as group_svc

        group_svc.create_group("main")
        self.flagged_ws = Workspace.objects.create(name="flagged-ws")
        self.other_ws = Workspace.objects.create(name="other-ws")
        group_svc.add_workspace_to_group("main", self.flagged_ws)
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
        entities=[
            Entity(name="Alice", type="PER", attributes={"role": "eng"}),
            Entity(name="Bob", type="PER", attributes={}),
        ]
        relations=[
            Relation(
                source="Alice",
                target="Bob",
                type_description="knows",
                description="Alice knows Bob",
            ),
        ]
        ingest_knowledge_graph(self.doc_flagged, entities, relations)
        ingest_knowledge_graph(
            self.doc_other,
            entities, 
            relations,
        )

    def test_build_workspace_graph_shape(self):
        graph = kg_graph.build_workspace_graph(self.flagged_ws)
        self.assertEqual(graph["workspace"], "flagged-ws")
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertIn("filters", graph)
        self.assertIn("truncated", graph)
        node = graph["nodes"][0]
        self.assertEqual(set(node.keys()), {"id", "name", "entity_type"})
        edge = graph["edges"][0]
        self.assertEqual(edge["source"], "Alice")
        self.assertEqual(edge["target"], "Bob")
        self.assertIn("source_id", edge)
        self.assertIn("target_id", edge)

    def test_group_bulk_includes_only_members(self):
        filters = kg_graph.GraphFilters(
            entity_types=None, file_names=None, depth=1, limit=500
        )
        graphs = kg_graph.build_filtered_graphs_for_group("main", filters)
        names = [g["workspace"] for g in graphs]
        self.assertIn("flagged-ws", names)
        self.assertNotIn("other-ws", names)

    def test_filtered_entity_type_and_depth_zero(self):
        filters = kg_graph.GraphFilters(
            entity_types=["ORG"], file_names=None, depth=0, limit=500
        )
        graph = kg_graph.build_filtered_workspace_graph(self.flagged_ws, filters)
        self.assertEqual(len(graph["nodes"]), 0)
        self.assertEqual(len(graph["edges"]), 0)

    def test_list_entity_types(self):
        payload = kg_graph.list_entity_types_for_workspace(self.flagged_ws)
        types = {row["type"]: row["count"] for row in payload["entity_types"]}
        self.assertEqual(types["PER"], 2)


class KnowledgeGraphAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ws = Workspace.objects.create(name="api-kg-ws")
        self.ws2 = Workspace.objects.create(name="api-kg-ws-2")
        self.doc = Document.objects.create(
            workspace=self.ws,
            file_name="doc.md",
            file=SimpleUploadedFile("doc.md", b"x"),
        )
        entities = [
            Entity(name="Alice", type="PER", attributes={}),
            Entity(name="Acme", type="ORG", attributes={}),
        ]
        relations = [
            Relation(
                source="Alice",
                target="Acme",
                type_description="works at",
                description="Alice works at Acme.",
            ),
        ]
        ingest_knowledge_graph(self.doc, entities, relations)
        self.doc_notes = Document.objects.create(
            workspace=self.ws,
            file_name="notes.md",
            file=SimpleUploadedFile("notes.md", b"n"),
        )
        ingest_knowledge_graph(
            self.doc_notes,
            [Entity(name="Carol", type="PER", attributes={})],
            [],
        )
        doc2 = Document.objects.create(
            workspace=self.ws2,
            file_name="other.md",
            file=SimpleUploadedFile("other.md", b"y"),
        )
        ingest_knowledge_graph(
            doc2,
            [Entity(name="Bob", type="PER", attributes={})],
            [],
        )

    def test_nodes_contain_only_id_name_entity_type(self):
        resp = self.client.get(
            "/api/knowledge-graph/",
            {"workspace_name": "api-kg-ws", "depth": "0"},
        )
        self.assertEqual(resp.status_code, 200)
        for node in resp.json()["nodes"]:
            self.assertEqual(set(node.keys()), {"id", "name", "entity_type"})

    def test_file_name_filter(self):
        resp = self.client.get(
            "/api/knowledge-graph/",
            {
                "workspace_name": "api-kg-ws",
                "file_name": "notes.md",
                "depth": "0",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["filters"]["file_names"], ["notes.md"])
        names = {n["name"] for n in data["nodes"]}
        self.assertEqual(names, {"Carol"})

    def test_file_name_csv(self):
        resp = self.client.get(
            "/api/knowledge-graph/",
            {
                "workspace_name": "api-kg-ws",
                "file_name": "doc.md,notes.md",
                "depth": "0",
            },
        )
        self.assertEqual(resp.status_code, 200)
        names = {n["name"] for n in resp.json()["nodes"]}
        self.assertEqual(names, {"Alice", "Acme", "Carol"})

    def test_empty_file_name_returns_400(self):
        resp = self.client.get(
            "/api/knowledge-graph/",
            {"workspace_name": "api-kg-ws", "file_name": "  , "},
        )
        self.assertEqual(resp.status_code, 400)

    def test_get_by_workspace_name(self):
        resp = self.client.get(
            "/api/knowledge-graph/", {"workspace_name": "api-kg-ws"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["workspace"], "api-kg-ws")
        self.assertEqual(len(data["nodes"]), 3)
        self.assertEqual(data["filters"]["depth"], 1)
        self.assertEqual(data["filters"]["limit"], 500)

    def test_get_by_group(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.create_group("pair")
        group_svc.add_workspace_to_group("pair", self.ws)
        group_svc.add_workspace_to_group("pair", self.ws2)
        resp = self.client.get("/api/knowledge-graph/", {"group": "pair"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["group"], "pair")
        workspaces = [g["workspace"] for g in data["graphs"]]
        self.assertIn("api-kg-ws", workspaces)
        self.assertIn("api-kg-ws-2", workspaces)

    def test_get_by_group_name(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.get_or_create_group("research")
        ws = Workspace.objects.create(name="research-only")
        group_svc.add_workspace_to_group("research", ws)
        resp = self.client.get(
            "/api/knowledge-graph/",
            {"group": "research", "depth": "0"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["group"], "research")
        self.assertEqual(len(data["graphs"]), 1)
        self.assertEqual(data["graphs"][0]["workspace"], "research-only")

    def test_entity_type_filter_per_only(self):
        resp = self.client.get(
            "/api/knowledge-graph/",
            {"workspace_name": "api-kg-ws", "entity_type": "PER", "depth": "0"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["nodes"]), 2)
        names = {n["name"] for n in data["nodes"]}
        self.assertEqual(names, {"Alice", "Carol"})
        self.assertEqual(data["edges"], [])

    def test_entity_type_csv_and_depth_one(self):
        resp = self.client.get(
            "/api/knowledge-graph/",
            {
                "workspace_name": "api-kg-ws",
                "entity_type": "PER,ORG",
                "depth": "1",
                "limit": "500",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        names = {n["name"] for n in data["nodes"]}
        self.assertEqual(names, {"Alice", "Acme", "Carol"})
        self.assertEqual(len(data["edges"]), 1)

    def test_limit_truncated(self):
        resp = self.client.get(
            "/api/knowledge-graph/",
            {"workspace_name": "api-kg-ws", "limit": "1", "depth": "0"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["nodes"]), 1)
        self.assertTrue(data["truncated"])

    def test_invalid_depth_returns_400(self):
        resp = self.client.get(
            "/api/knowledge-graph/",
            {"workspace_name": "api-kg-ws", "depth": "99"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_entity_type_returns_400(self):
        resp = self.client.get(
            "/api/knowledge-graph/",
            {"workspace_name": "api-kg-ws", "entity_type": "  , "},
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_params_returns_400(self):
        resp = self.client.get("/api/knowledge-graph/")
        self.assertEqual(resp.status_code, 400)


class KnowledgeGraphEntityTypesAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ws = Workspace.objects.create(name="types-ws")
        self.doc = Document.objects.create(
            workspace=self.ws,
            file_name="doc.md",
            file=SimpleUploadedFile("doc.md", b"x"),
        )
        ingest_knowledge_graph(
            self.doc,
            [
                Entity(name="Alice", type="PER", attributes={}),
                Entity(name="Bob", type="PER", attributes={}),
                Entity(name="Acme", type="ORG", attributes={}),
            ],
            [],
        )

    def test_entity_types_by_workspace(self):
        resp = self.client.get(
            "/api/knowledge-graph/entity-types/",
            {"workspace_name": "types-ws"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["workspace"], "types-ws")
        types = {row["type"]: row["count"] for row in data["entity_types"]}
        self.assertEqual(types["PER"], 2)
        self.assertEqual(types["ORG"], 1)

    def test_entity_types_by_group(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.add_workspace_to_group("flagged", self.ws)
        resp = self.client.get(
            "/api/knowledge-graph/entity-types/", {"group": "flagged"}
        )
        self.assertEqual(resp.status_code, 200)
        names = [w["workspace"] for w in resp.json()["workspaces"]]
        self.assertIn("types-ws", names)

    def test_entity_types_missing_scope_400(self):
        resp = self.client.get("/api/knowledge-graph/entity-types/")
        self.assertEqual(resp.status_code, 400)

    def test_entity_types_unknown_workspace_404(self):
        resp = self.client.get(
            "/api/knowledge-graph/entity-types/",
            {"workspace_name": "missing"},
        )
        self.assertEqual(resp.status_code, 404)


class KnowledgeEntitySearchAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ws = Workspace.objects.create(name="entity-search-ws")
        self.doc = Document.objects.create(
            workspace=self.ws,
            file_name="doc.md",
            file=SimpleUploadedFile("doc.md", b"x"),
        )
        entities = [
            Entity(name="Alice", type="PER", attributes={}),
            Entity(name="Alicia", type="PER", attributes={}),
            Entity(name="Acme Corp", type="ORG", attributes={}),
        ]
        relations = [
            Relation(
                source="Alice",
                target="Acme Corp",
                type_description="works at",
                description="Alice works at Acme.",
            ),
        ]
        ingest_knowledge_graph(self.doc, entities, relations)
        self.doc_other = Document.objects.create(
            workspace=self.ws,
            file_name="other.md",
            file=SimpleUploadedFile("other.md", b"y"),
        )
        ingest_knowledge_graph(
            self.doc_other,
            [Entity(name="Acme Corp", type="ORG", attributes={})],
            [],
        )

    def test_fuzzy_search_returns_matches_and_graph(self):
        resp = self.client.get(
            "/api/knowledge/entities/search/",
            {
                "q": "Alcie",
                "workspace_name": "entity-search-ws",
                "threshold": "0.6",
                "depth": "1",
                "limit": "50",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["query"], "Alcie")
        self.assertGreaterEqual(len(data["matches"]), 1)
        names = {m["name"] for m in data["matches"]}
        self.assertTrue(names & {"Alice", "Alicia"})
        self.assertGreaterEqual(data["matches"][0]["score"], 0.6)
        for match in data["matches"]:
            self.assertEqual(
                set(match.keys()),
                {"id", "name", "entity_type", "score", "workspace"},
            )
        graph = data["graph"]
        self.assertIn("nodes", graph)
        self.assertIn("edges", graph)
        for node in graph["nodes"]:
            self.assertEqual(set(node.keys()), {"id", "name", "entity_type"})

    def test_entity_search_file_name_filter(self):
        resp_all = self.client.get(
            "/api/knowledge/entities/search/",
            {
                "q": "Acme Corp",
                "workspace_name": "entity-search-ws",
                "threshold": "0.9",
            },
        )
        self.assertEqual(resp_all.status_code, 200)
        self.assertEqual(len(resp_all.json()["matches"]), 2)

        resp = self.client.get(
            "/api/knowledge/entities/search/",
            {
                "q": "Acme Corp",
                "workspace_name": "entity-search-ws",
                "file_name": "doc.md",
                "threshold": "0.9",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["matches"]), 1)
        self.assertEqual(data["matches"][0]["name"], "Acme Corp")

    def test_threshold_excludes_weak_matches(self):
        resp = self.client.get(
            "/api/knowledge/entities/search/",
            {
                "q": "zzz",
                "workspace_name": "entity-search-ws",
                "threshold": "0.9",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["matches"], [])
        self.assertEqual(resp.json()["graph"]["nodes"], [])

    def test_missing_q_returns_400(self):
        resp = self.client.get(
            "/api/knowledge/entities/search/",
            {"workspace_name": "entity-search-ws"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_group_scope(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.add_workspace_to_group("flagged", self.ws)
        resp = self.client.get(
            "/api/knowledge/entities/search/",
            {"q": "Alice", "group": "flagged"},
        )
        self.assertEqual(resp.status_code, 200)
        names = [w["workspace"] for w in resp.json()["workspaces"]]
        self.assertIn("entity-search-ws", names)


class KgEntitySearchServiceTests(TestCase):
    def setUp(self):
        self.ws = Workspace.objects.create(name="fuzzy-svc-ws")
        self.doc = Document.objects.create(
            workspace=self.ws,
            file_name="d.md",
            file=SimpleUploadedFile("d.md", b"x"),
        )
        ingest_knowledge_graph(
            self.doc,
            [Entity(name="Nmap", type="TOOL", attributes={})],
            [],
        )

    def test_fuzzy_match_typo(self):
        from nodepoint.services.kg_entity_search import fuzzy_match_entities

        matches = fuzzy_match_entities(
            "Nmapp",
            ["fuzzy-svc-ws"],
            threshold=0.6,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["name"], "Nmap")

    def test_typo_when_icontains_prefilter_misses(self):
        """Alcie does not icontains-match Alice; broad sample must still find it."""
        from nodepoint.services.kg_entity_search import fuzzy_match_entities, score_entity_name

        doc = Document.objects.create(
            workspace=self.ws,
            file_name="noise.md",
            file=SimpleUploadedFile("noise.md", b"x"),
        )
        noise = [Entity(name=f"Alert-{i}", type="TECH", attributes={}) for i in range(30)]
        ingest_knowledge_graph(
            doc,
            noise + [Entity(name="Alice", type="PER", attributes={})],
            [],
        )
        self.assertGreaterEqual(score_entity_name("Alcie", "Alice"), 0.6)
        matches = fuzzy_match_entities("Alcie", ["fuzzy-svc-ws"], threshold=0.6)
        self.assertTrue(any(m["name"] == "Alice" for m in matches))


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

    def test_messages_on_internal_branch_mirror_to_root(self):
        conversation, root = chat_storage.get_or_create_workspace_chat(self.ws)
        internal = chat_storage.create_branch_from_compression(
            conversation, root, "hidden compression report"
        )
        chat_storage.append_message_visible(
            conversation.id,
            internal.id,
            role=ChatMessageRole.USER,
            content="after compress user",
        )
        chat_storage.append_message_visible(
            conversation.id,
            internal.id,
            role=ChatMessageRole.ASSISTANT,
            content="after compress assistant",
        )
        resp = self.client.get("/api/chat/detail-ws/")
        contents = [m["content"] for m in resp.json()["messages"]]
        self.assertIn("after compress user", contents)
        self.assertIn("after compress assistant", contents)
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
        self.ws = Workspace.objects.create(name="summary-ws")
        chat_storage.create_conversation(self.ws)

    def test_summary_by_workspace(self):
        resp = self.client.get("/api/chat/summary/", {"workspace_name": "summary-ws"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["workspace"], "summary-ws")
        self.assertIn("message_count", data)
        self.assertGreaterEqual(data["message_count"], 1)
        self.assertNotIn("conversations", data)

    def test_summary_by_group(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.add_workspace_to_group("flagged", self.ws)
        resp = self.client.get("/api/chat/summary/", {"group": "flagged"})
        self.assertEqual(resp.status_code, 200)
        names = [w["workspace"] for w in resp.json()["workspaces"]]
        self.assertIn("summary-ws", names)


class ChatContextSearchScopeTests(TestCase):
    def test_resolve_per_workspace_chat_is_active_workspace_only(self):
        Workspace.objects.create(name="chat-only")
        Workspace.objects.create(name="flag-a")
        Workspace.objects.create(name="flag-b")

        token = set_chat_workspace("chat-only")
        try:
            names = resolve_search_workspace_names()
        finally:
            reset_chat_workspace(token)

        self.assertEqual(names, ["chat-only"])

    def test_resolve_excludes_other_workspaces_in_per_workspace_chat(self):
        Workspace.objects.create(name="other")
        Workspace.objects.create(name="flag-a")

        token = set_chat_workspace("other")
        try:
            names = resolve_search_workspace_names()
        finally:
            reset_chat_workspace(token)

        self.assertEqual(names, ["other"])
        self.assertNotIn("flag-a", names)

    def test_group_scope_chat_searches_group_members(self):
        from nodepoint.services import workspace_group as group_svc
        from nodepoint.services.chat_context import (
            reset_group_scope_chat,
            set_group_scope_chat,
        )

        group_svc.create_group("stars")
        Workspace.objects.create(name="chat-only")
        flag_a = Workspace.objects.create(name="flag-a")
        group_svc.add_workspace_to_group("stars", flag_a)

        token = set_group_scope_chat("stars")
        try:
            names = resolve_search_workspace_names()
        finally:
            reset_group_scope_chat(token)

        self.assertEqual(names, ["flag-a"])

    def test_custom_group_scope_searches_member_workspaces(self):
        from nodepoint.services import workspace_group as group_svc
        from nodepoint.services.chat_context import (
            reset_group_scope_chat,
            set_group_scope_chat,
        )

        group_svc.create_group("research")
        Workspace.objects.create(name="in-group")
        ws = Workspace.objects.get(name="in-group")
        group_svc.add_workspace_to_group("research", ws)
        Workspace.objects.create(name="out-group")

        token = set_group_scope_chat("research")
        try:
            names = resolve_search_workspace_names()
        finally:
            reset_group_scope_chat(token)

        self.assertEqual(names, ["in-group"])


class KgSearchTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="kg-search-ws")
        self.document = Document.objects.create(
            workspace=self.workspace,
            file_name="notes.md",
            file=SimpleUploadedFile("notes.md", b"# doc"),
        )
        entities=[Entity(name="Alice", type="PER", attributes={"role": "eng"})]
        relations=[]
        ingest_knowledge_graph(self.document, entities, relations)

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
        self.assertIn("[entity](", doc)
        self.assertIn("Alice", doc)

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


class GroupChatAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_group_chat_empty_members(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.create_group("empty-chat")
        resp = self.client.get("/api/chat/group/empty-chat/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["group"], "empty-chat")
        self.assertEqual(data["workspaces"], [])
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["role"], "system")

    def test_group_chat_separate_from_named_workspace(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.create_group("g1")
        Workspace.objects.create(name="123")
        chat_storage.get_or_create_workspace_chat(Workspace.objects.get(name="123"))
        group_resp = self.client.get("/api/chat/group/g1/")
        ws_resp = self.client.get("/api/chat/123/")
        self.assertEqual(group_resp.status_code, 200)
        self.assertEqual(ws_resp.status_code, 200)
        self.assertEqual(group_resp.json()["group"], "g1")
        self.assertEqual(ws_resp.json()["workspace"], "123")

    def test_group_chat_lists_member_workspaces(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.create_group("listed")
        older = Workspace.objects.create(name="older")
        newer = Workspace.objects.create(name="newer")
        group_svc.add_workspace_to_group("listed", older)
        group_svc.add_workspace_to_group("listed", newer)
        resp = self.client.get("/api/chat/group/listed/")
        self.assertEqual(resp.json()["workspaces"], ["older", "newer"])


class WorkspaceGroupAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_create_and_list_groups(self):
        resp = self.client.post(
            "/api/group/create/",
            {"name": "research"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get("/api/group/list/")
        self.assertEqual(resp.status_code, 200)
        names = [g["name"] for g in resp.json()["groups"]]
        self.assertIn("research", names)

    def test_add_remove_workspace_and_detail(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.create_group("team-a")
        ws = Workspace.objects.create(name="member-ws")
        add_resp = self.client.post(
            "/api/group/team-a/workspaces/",
            {"workspace_name": "member-ws"},
            format="json",
        )
        self.assertEqual(add_resp.status_code, 200)
        detail = self.client.get("/api/group/team-a/")
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertEqual(body["workspace_count"], 1)
        self.assertEqual(body["workspaces"][0]["name"], "member-ws")
        self.assertIn("pagination", body)

        rm_resp = self.client.delete("/api/group/team-a/workspaces/member-ws/")
        self.assertEqual(rm_resp.status_code, 200)
        detail2 = self.client.get("/api/group/team-a/")
        self.assertEqual(detail2.json()["workspace_count"], 0)

    def test_delete_group(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.create_group("temp")
        resp = self.client.delete("/api/group/temp/")
        self.assertEqual(resp.status_code, 200)

    def test_group_chat_endpoint(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.create_group("chat-group")
        resp = self.client.get("/api/chat/group/chat-group/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["group"], "chat-group")


class UploadDefaultWorkspaceTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch("nodepoint.views.document.enqueue_preprocess_pipeline")
    def test_upload_without_workspace_uses_oldest_workspace(self, mock_pipeline):
        Workspace.objects.create(name="first-star")
        Workspace.objects.create(name="second-star")
        mock_pipeline.return_value = {"message": "ok", "steps": [], "jobs": {}}
        resp = self.client.post(
            "/api/document/upload/",
            {"file": SimpleUploadedFile("note.md", b"content")},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 200)
        from nodepoint.models import Document

        doc = Document.objects.get(id=resp.json()["id"])
        self.assertEqual(doc.workspace.name, "first-star")

    def test_upload_without_workspace_400_when_none_exist(self):
        resp = self.client.post(
            "/api/document/upload/",
            {"file": SimpleUploadedFile("note.md", b"content")},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)


class WorkspaceCatalogAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ws_a = Workspace.objects.create(name="cat-ws-a")
        self.ws_b = Workspace.objects.create(name="cat-ws-b")
        doc = Document.objects.create(
            workspace=self.ws_a,
            file_name="f.md",
            file=SimpleUploadedFile("f.md", b"x"),
        )
        ingest_knowledge_graph(
            doc,
            [
                Entity(name="N1", type="PER", attributes={}),
                Entity(name="N2", type="ORG", attributes={}),
            ],
            [
                Relation(
                    source="N1",
                    target="N2",
                    type_description="rel",
                    description="N1 to N2 link.",
                ),
            ],
        )
        DocumentChunk.objects.create(
            document=doc,
            index=0,
            status=Status.COMPLETED,
        )

    def test_workspace_stats(self):
        resp = self.client.get("/api/workspace/stats/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["total"], 2)
        self.assertEqual(data["in_group"] + data["ungrouped"], data["total"])

    def test_workspace_page_all(self):
        resp = self.client.get(
            "/api/workspace/page/", {"page": "1", "page_size": "10"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["group"])
        names = [w["name"] for w in data["workspaces"]]
        self.assertIn("cat-ws-a", names)
        row = next(w for w in data["workspaces"] if w["name"] == "cat-ws-a")
        self.assertEqual(row["counts"]["files"], 1)
        self.assertEqual(row["counts"]["entities"], 2)
        self.assertEqual(row["counts"]["relations"], 1)
        self.assertEqual(row["counts"]["chunks"], 1)

    def test_workspace_page_group_filter(self):
        from nodepoint.services import workspace_group as group_svc

        group_svc.create_group("page-filter")
        group_svc.add_workspace_to_group("page-filter", self.ws_a)
        resp = self.client.get("/api/workspace/page/", {"group": "page-filter"})
        self.assertEqual(resp.status_code, 200)
        names = [w["name"] for w in resp.json()["workspaces"]]
        self.assertEqual(names, ["cat-ws-a"])

    def test_workspace_page_without_counts(self):
        resp = self.client.get(
            "/api/workspace/page/",
            {"page": "1", "page_size": "10", "include_counts": "false"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["include_counts"])
        row = next(w for w in data["workspaces"] if w["name"] == "cat-ws-a")
        self.assertNotIn("counts", row)

    def test_workspace_list_is_paginated_without_counts(self):
        resp = self.client.get(
            "/api/workspace/list/", {"page": "1", "page_size": "10"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("pagination", data)
        self.assertIn("workspaces", data)
        self.assertFalse(data["include_counts"])


class KnowledgeToolGroupScopeTests(TestCase):
    @patch("nodepoint.registry.tools.Knowledge.resolve_search_workspace_names")
    def test_search_graph_empty_group_message(self, mock_resolve):
        mock_resolve.return_value = []
        from nodepoint.registry.tools import Knowledge as knowledge_tools

        result = knowledge_tools.search_graph("query")
        self.assertIn("No workspace", result)


class ChatRunnerPersistenceTests(TestCase):
    @patch("nodepoint.services.chat_runner.chat_compression.compress_async", new_callable=AsyncMock)
    @patch.object(chat_runner.Agent, "stream_agent_events_async")
    def test_flush_partial_assistant_on_cancel(self, mock_stream, mock_compress):
        from asgiref.sync import async_to_sync
        from nodepoint.agent.schema import AssistantResponseTokenEvent
        from nodepoint.services.chat_runner import run_agent_stream

        async def stream_then_cancel(*args, **kwargs):
            yield AssistantResponseTokenEvent(token="Partial ")
            yield AssistantResponseTokenEvent(token="answer")
            raise asyncio.CancelledError()

        mock_stream.side_effect = stream_then_cancel
        mock_compress.return_value = "summary"

        workspace = Workspace.objects.create(name="partial-save-ws")
        conversation, root = chat_storage.create_conversation(workspace)
        thread, _, _ = chat_storage.load_thread(root.id)

        with patch.dict(
            "os.environ",
            {"BASE_URL": "http://test", "API_KEY": "test-key"},
        ):
            with self.assertRaises(asyncio.CancelledError):
                async_to_sync(run_agent_stream)(
                    thread,
                    chat_runner.Agent(),
                    root.id,
                    conversation.id,
                    tools=[],
                    on_event=AsyncMock(),
                )

        messages = chat_storage.load_branch_messages(root.id)
        assistant = [m for m in messages if m.role == "assistant"]
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant[0].content, "Partial answer")

    @patch("nodepoint.services.chat_runner.chat_compression.compress_async", new_callable=AsyncMock)
    @patch.object(chat_runner.Agent, "stream_agent_events_async")
    def test_turn_completes_normally_without_duplicate_assistant(self, mock_stream, mock_compress):
        from asgiref.sync import async_to_sync
        from nodepoint.agent.schema import (
            AgentSessionDoneEvent,
            AssistantResponseTokenEvent,
        )
        from nodepoint.services.chat_runner import run_agent_stream

        async def stream(*args, **kwargs):
            yield AssistantResponseTokenEvent(token="Done")
            yield AgentSessionDoneEvent()

        mock_stream.side_effect = stream
        mock_compress.return_value = "summary"

        workspace = Workspace.objects.create(name="done-save-ws")
        conversation, root = chat_storage.create_conversation(workspace)
        thread, _, _ = chat_storage.load_thread(root.id)

        with patch.dict(
            "os.environ",
            {"BASE_URL": "http://test", "API_KEY": "test-key"},
        ):
            async_to_sync(run_agent_stream)(
                thread,
                chat_runner.Agent(),
                root.id,
                conversation.id,
                tools=[],
                on_event=AsyncMock(),
            )

        assistant = [
            m
            for m in chat_storage.load_branch_messages(root.id)
            if m.role == "assistant"
        ]
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant[0].content, "Done")


class ChatCompressionTests(TestCase):
    def test_build_compression_thread_truncates_tool_content(self):
        from nodepoint.agent.schema import ToolMessage
        from nodepoint.registry import Thread
        from nodepoint.services.chat_compression import build_compression_thread

        thread = Thread()
        thread.addSystem("chat system")
        thread.addUser("hello")
        huge = "x" * 10000
        thread.addTool(
            type("T", (), {"id": "call-1"})(),
            huge,
        )
        slim = build_compression_thread(thread)
        tool_msgs = [m for m in slim.messages if isinstance(m, ToolMessage)]
        self.assertEqual(len(tool_msgs), 1)
        self.assertLess(len(tool_msgs[0].content), 5000)
        self.assertIn("truncated for compression", tool_msgs[0].content)

    @patch.object(chat_runner.Agent, "invoke_compression_async", new_callable=AsyncMock)
    def test_maybe_compress_continues_on_failure(self, mock_compress):
        from asgiref.sync import async_to_sync
        from nodepoint.agent.schema import AgentSessionDoneEvent
        from nodepoint.registry import Thread
        from nodepoint.services.chat_runner import run_agent_stream

        mock_compress.side_effect = RuntimeError("upstream 500")

        async def empty_stream(*args, **kwargs):
            yield AgentSessionDoneEvent()

        workspace = Workspace.objects.create(name="compress-fail-ws")
        conversation, root = chat_storage.create_conversation(workspace)
        thread, _, _ = chat_storage.load_thread(root.id)

        events: list[dict] = []

        async def on_event(payload):
            events.append(payload)

        with patch.object(chat_runner.Agent, "stream_agent_events_async", side_effect=empty_stream):
            with patch.dict(
                "os.environ",
                {"BASE_URL": "http://test", "API_KEY": "test-key", "CHAT_COMPRESS_TOKEN_THRESHOLD": "1"},
            ):
                with patch.object(Thread, "count_tokens", return_value=99999):
                    async_to_sync(run_agent_stream)(
                        thread,
                        chat_runner.Agent(),
                        root.id,
                        conversation.id,
                        tools=[],
                        on_event=on_event,
                    )

        types = [e.get("type") for e in events]
        self.assertIn("chat.compress_started", types)
        self.assertIn("chat.compress_failed", types)
        self.assertNotIn("chat.compress_completed", types)
        self.assertNotIn("chat.compressed", types)

    def test_invoke_compression_omits_reasoning_by_default(self):
        from nodepoint.registry import Thread

        agent = chat_runner.Agent.__new__(chat_runner.Agent)
        agent.model = "test-model"
        agent.base_url = "http://test/v1"
        agent.verify_ssl = False
        agent.session = MagicMock()
        agent.skip_model_validation = True
        agent._model_ids = {"test-model"}

        thread = Thread()
        thread.addUser("summarize")

        with patch.dict("os.environ", {"CHAT_COMPRESS_OMIT_REASONING": "true"}, clear=False):
            with patch.object(agent, "_request", return_value={
                "choices": [{"message": {"content": "summary"}, "finish_reason": "stop"}],
                "usage": {},
            }) as mock_req:
                with patch.object(agent, "_validate_model"):
                    with patch.object(agent, "_resolve_model", return_value="test-model"):
                        agent.invoke_compression(thread)

        payload = mock_req.call_args[0][2]
        self.assertNotIn("reasoning", payload)
        self.assertEqual(payload.get("max_tokens"), 4000)

    def test_cap_summary_tokens(self):
        from nodepoint.services.chat_compression import cap_summary_tokens

        text = "word " * 5000
        capped = cap_summary_tokens(text, max_tokens=50)
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        self.assertLessEqual(len(enc.encode(capped)), 55)
        self.assertIn("summary capped", capped)


class WebSocketStreamReconnectTests(TransactionTestCase):
    @patch("nodepoint.services.chat_runner.chat_compression.compress_async", new_callable=AsyncMock)
    @patch.object(chat_runner.Agent, "stream_agent_events_async")
    def test_second_socket_receives_live_stream_after_disconnect(
        self, mock_stream, mock_compress
    ):
        from asgiref.sync import async_to_sync
        from channels.testing import WebsocketCommunicator
        from config.asgi import application
        from nodepoint.agent.schema import (
            AgentSessionDoneEvent,
            AssistantResponseTokenEvent,
        )

        mock_compress.return_value = "summary"

        async def delayed_stream(*args, **kwargs):
            await asyncio.sleep(0.15)
            yield AssistantResponseTokenEvent(token="Live")
            yield AgentSessionDoneEvent()

        mock_stream.side_effect = delayed_stream

        Workspace.objects.create(name="ws-reconnect-live")

        async def run():
            with patch.dict(
                "os.environ",
                {"BASE_URL": "http://test", "API_KEY": "test-key"},
            ):
                comm1 = WebsocketCommunicator(
                    application, "/ws/chat/ws-reconnect-live/"
                )
                connected, _ = await comm1.connect()
                self.assertTrue(connected)
                ready1 = await comm1.receive_json_from(timeout=2)
                self.assertEqual(ready1["type"], "chat.ready")

                await comm1.send_json_to(
                    {"type": "chat.send", "content": "hello"}
                )
                started = await comm1.receive_json_from(timeout=2)
                self.assertEqual(started["type"], "chat.turn_started")

                await comm1.disconnect()

                comm2 = WebsocketCommunicator(
                    application, "/ws/chat/ws-reconnect-live/"
                )
                connected2, _ = await comm2.connect()
                self.assertTrue(connected2)
                ready2 = await comm2.receive_json_from(timeout=2)
                self.assertEqual(ready2["type"], "chat.ready")
                self.assertTrue(ready2.get("agent_busy"))

                saw_live = False
                saw_done = False
                for _ in range(20):
                    msg = await asyncio.wait_for(
                        comm2.receive_json_from(), timeout=2
                    )
                    if msg.get("type") == "assistant_response_token":
                        if msg.get("token") == "Live":
                            saw_live = True
                    if msg.get("type") == "chat.done":
                        saw_done = True
                        break

                await comm2.disconnect()
                self.assertTrue(saw_live, "reconnected socket should receive live tokens")
                self.assertTrue(saw_done)

        async_to_sync(run)()

    def test_chat_reconnect_when_idle(self):
        from asgiref.sync import async_to_sync
        from channels.testing import WebsocketCommunicator
        from config.asgi import application

        Workspace.objects.create(name="ws-reconnect-idle")

        async def run():
            comm = WebsocketCommunicator(
                application, "/ws/chat/ws-reconnect-idle/"
            )
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from(timeout=2)
            await comm.send_json_to({"type": "chat.reconnect"})
            reply = await comm.receive_json_from(timeout=2)
            self.assertEqual(reply["type"], "chat.reconnected")
            self.assertFalse(reply.get("agent_busy"))
            await comm.disconnect()

        async_to_sync(run)()


class AsyncChatConcurrencyTests(TestCase):
    @patch("nodepoint.services.chat_runner.chat_compression.compress_async", new_callable=AsyncMock)
    @patch.object(chat_runner.Agent, "stream_agent_events_async")
    @patch("asyncio.to_thread")
    def test_maybe_compress_counts_tokens_off_event_loop(
        self, mock_to_thread, mock_stream, mock_compress
    ):
        import time
        from nodepoint.services.chat_runner import run_agent_stream

        async def empty_stream(*args, **kwargs):
            yield AgentSessionDoneEvent()

        mock_stream.return_value = empty_stream()
        mock_compress.return_value = "summary"

        def to_thread_side_effect(func, *args, **kwargs):
            if getattr(func, "__name__", "") == "root_count_tokens":
                return 100_000
            if func is time.sleep:
                return None
            return func(*args, **kwargs)

        mock_to_thread.side_effect = to_thread_side_effect

        workspace = Workspace.objects.create(name="async-compress-ws")
        conversation, root = chat_storage.create_conversation(workspace)
        thread, _, _ = chat_storage.load_thread(root.id)

        with patch.dict(
            "os.environ",
            {"BASE_URL": "http://test", "API_KEY": "test-key", "CHAT_COMPRESS_TOKEN_THRESHOLD": "1000"},
        ):
            async_to_sync(run_agent_stream)(
                thread,
                chat_runner.Agent(),
                root.id,
                conversation.id,
                tools=[],
                on_event=AsyncMock(),
            )

        token_calls = [
            c
            for c in mock_to_thread.call_args_list
            if c.args and getattr(c.args[0], "__name__", "") == "root_count_tokens"
        ]
        self.assertTrue(token_calls)

    def test_search_semaphore_limits_parallelism(self):
        import threading
        import time

        from nodepoint.services.chat_concurrency import run_with_search_limit

        active = 0
        peak = 0
        lock = threading.Lock()

        def slow_sync():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return "ok"

        import asyncio as aio

        async def run():
            return await aio.gather(*[run_with_search_limit(slow_sync) for _ in range(12)])

        results = async_to_sync(run)()
        self.assertEqual(len(results), 12)
        self.assertLessEqual(peak, 8)


class ChatRunnerToolTests(TestCase):
    def test_default_tools_includes_knowledge_suite(self):
        from nodepoint.services import chat_runner

        names = {t["function"]["name"] for t in chat_runner.default_tools()}
        self.assertIn("Knowledge.search_graph", names)
        self.assertIn("Knowledge.get_entity_record", names)
        self.assertIn("Knowledge.search_entity_by_name", names)
        self.assertEqual(len(names), 5)


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
    @patch("nodepoint.registry.tools.Knowledge.kg_search.build_search_document_from_records")
    @patch("nodepoint.registry.tools.Knowledge.hybrid_search")
    @patch("nodepoint.registry.tools.Knowledge.resolve_search_workspace_names")
    def test_invoke_async_runs_local_tool_off_event_loop(
        self, mock_resolve, mock_hybrid, mock_build
    ):
        from nodepoint.agent.agent import ToolCallNormalized
        from nodepoint.registry.tool import Tool

        mock_resolve.return_value = ["global"]
        mock_hybrid.return_value = []
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
    @patch("nodepoint.registry.tools.Knowledge.kg_search.build_search_document_from_records")
    @patch("nodepoint.registry.tools.Knowledge.hybrid_search")
    @patch("nodepoint.registry.tools.Knowledge.resolve_search_workspace_names")
    def test_search_graph_returns_markdown_document(
        self, mock_resolve, mock_hybrid, mock_build
    ):
        mock_resolve.return_value = ["chat-ws"]
        mock_hybrid.return_value = [
            {
                "kind": "entity",
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "score": 0.9,
                "scores": {"total": 0.9, "semantic": 0.8, "bm25": 0.5, "fuzzy": 0.4, "lexical": 0.45},
            }
        ]
        mock_build.return_value = "## Result 1 — entity [entity](550e8400)\n"

        from nodepoint.registry.tools import Knowledge as knowledge_tools

        result = knowledge_tools.search_graph("query", limit=5)
        self.assertIn("[entity](550e8400)", result)
        mock_build.assert_called_once()

    @patch("nodepoint.registry.tools.Knowledge.resolve_search_workspace_names")
    def test_search_graph_empty_when_no_workspaces(self, mock_resolve):
        mock_resolve.return_value = []
        from nodepoint.registry.tools import Knowledge as knowledge_tools

        result = knowledge_tools.search_graph("query")
        self.assertIn("No workspace", result)


class KgRecordsTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="kg-rec-ws")
        self.document = Document.objects.create(
            workspace=self.workspace,
            file_name="notes.md",
            file=SimpleUploadedFile("notes.md", b"# doc"),
        )
        self.chunk = DocumentChunk.objects.create(
            document=self.document, index=0, status=Status.COMPLETED
        )
        self.entity = KnowledgeEntity.objects.create(
            document=self.document,
            chunk=self.chunk,
            name="Alice",
            entity_type="PER",
            attributes={"role": "eng"},
        )
        self.entity_b = KnowledgeEntity.objects.create(
            document=self.document,
            name="Bob",
            entity_type="PER",
        )
        KnowledgeRelation.objects.create(
            document=self.document,
            chunk=self.chunk,
            source=self.entity,
            target=self.entity_b,
            type_description="knows",
            description="Alice knows Bob",
        )

    def test_serialize_entity_includes_chunk_id(self):
        from nodepoint.services.kg_records import serialize_entity

        data = serialize_entity(self.entity)
        self.assertEqual(data["chunk_id"], str(self.chunk.id))
        self.assertIn("Alice", data["content"])

    def test_get_entity_not_found(self):
        from nodepoint.services.kg_records import RecordNotFoundError, get_entity

        with self.assertRaises(RecordNotFoundError):
            get_entity(uuid.uuid4())

    def test_search_entities_by_name_with_relations(self):
        from nodepoint.services.kg_records import search_entities_by_name

        matches = search_entities_by_name("Alice", ["kg-rec-ws"], exact=True)
        self.assertEqual(len(matches), 1)
        self.assertTrue(any(r["direction"] == "outgoing" for r in matches[0]["relations"]))


class KgHybridSearchTests(TestCase):
    def test_rerank_lexical_can_change_order(self):
        from nodepoint.services.kg_hybrid_search import rerank_records

        records = [
            {
                "id": "a",
                "kind": "entity",
                "name": "zebra",
                "entity_type": "X",
                "attributes": {},
                "score": 0.99,
            },
            {
                "id": "b",
                "kind": "entity",
                "name": "Alice",
                "entity_type": "PER",
                "attributes": {"role": "eng"},
                "score": 0.1,
            },
        ]
        out = rerank_records(
            "Alice engineer",
            records,
            semantic_weight=0.0,
            lexical_weight=1.0,
        )
        self.assertEqual(out[0]["id"], "b")

    def test_normalize_weights(self):
        from nodepoint.services.kg_hybrid_search import normalize_weights

        sem, lex = normalize_weights(3, 1)
        self.assertAlmostEqual(sem + lex, 1.0)


class KgRecordAPITests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.workspace = Workspace.objects.create(name="api-kg-rec")
        self.document = Document.objects.create(
            workspace=self.workspace,
            file_name="doc.md",
            file=SimpleUploadedFile("doc.md", b"x"),
        )
        self.entity = KnowledgeEntity.objects.create(
            document=self.document,
            name="Node",
            entity_type="TECH",
        )

    def test_get_entity_by_id(self):
        resp = self.client.get(f"/api/knowledge/entity/{self.entity.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "Node")

    def test_get_entity_404(self):
        resp = self.client.get(f"/api/knowledge/entity/{uuid.uuid4()}/")
        self.assertEqual(resp.status_code, 404)

    @patch("nodepoint.services.kg_records.get_document_text", return_value="full doc body")
    def test_get_document_by_id(self, mock_text):
        resp = self.client.get(f"/api/knowledge/document/{self.document.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kind"], "doc")
        self.assertEqual(resp.json()["content"], "full doc body")


class ChatPromptCitationTests(TestCase):
    def test_chat_system_requires_id_citations(self):
        from nodepoint.registry.prompt import Prompt

        prompt = Prompt["chat_system"]
        self.assertIn("[entity](", prompt)
        self.assertIn("[doc](", prompt)
        self.assertIn("Never cite with [source: file_name]", prompt)
        self.assertIn("Knowledge.get_document_record", prompt)


class KgSearchFormatTests(TestCase):
    def test_format_includes_scores_and_chunk_id(self):
        from nodepoint.services.kg_search import format_search_document

        md = format_search_document(
            [
                {
                    "kind": "entity",
                    "id": "e1",
                    "chunk_id": "c1",
                    "file_name": "a.md",
                    "workspace": "ws",
                    "content": "name: Alice",
                    "scores": {
                        "total": 0.8,
                        "semantic": 0.7,
                        "bm25": 0.5,
                        "fuzzy": 0.4,
                        "lexical": 0.45,
                    },
                }
            ],
            "alice",
        )
        self.assertIn("[entity](e1)", md)
        self.assertIn("**cite**", md)
        self.assertIn("[doc](", md)
        self.assertIn("Never use [source: file_name]", md)
