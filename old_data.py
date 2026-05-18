import psycopg2
from psycopg2.extras import Json, RealDictCursor
import json
from psycopg2.extensions import register_adapter, AsIs
import numpy as np
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
import traceback
import psycopg2.extras
psycopg2.extras.register_uuid()


# -------------------- CONFIG -------------------- #
DB_NAME = "prajna"
USER = "butcher"
PASSWORD = "qwer"
HOST = "localhost"
PORT = "5433"

def addapt_numpy_array(numpy_array):
    return AsIs("ARRAY[%s]" % ",".join(map(str, numpy_array)))
register_adapter(np.ndarray, addapt_numpy_array)


# -------------------- BASE MODEl CLASSES ------------------- #
class MemoryIn(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workspace: str
    query: str
    ai: str
    source: Optional[List] = None
    embedding: List[float]
    request_time: datetime
    response_time: datetime = Field(default_factory=datetime.now)
    engine: str
    filter: Optional[dict | list] = {"files": "all"}
    
class QueryIn(BaseModel):
    id: str
    workspace: str
    query: str
    engine: str
    filter: Optional[Dict] = {"files": "all"}

class HELPER():
    # -------------------- CONNECTION HELPER -------------------- #
    def get_connection(self):
        """Connect to PostgreSQL and return connection with RealDictCursor (dict results)."""
        return psycopg2.connect(
            dbname=DB_NAME,
            user=USER,
            password=PASSWORD,
            host=HOST,
            port=PORT,
            cursor_factory=RealDictCursor  # this makes results return as dicts
        )



    # -------------------- WORKSPACE -------------------- #
    def create_workspace(self, workspace_name: str):
        """Create a new workspace."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO workspace (workspace_name) VALUES (%s) ON CONFLICT DO NOTHING;",
            (workspace_name,)
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Workspace '{workspace_name}' saved.")


    def get_all_workspaces(self, as_json=False):
        """Get all workspaces as list of dicts or JSON string."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT workspace_name, timestamp, star FROM workspace;")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        # Since rows are dictionaries (RealDictCursor), use keys instead of indexes
        workspaces = [
            {"workspace_name": r["workspace_name"], "timestamp": r["timestamp"], "star": r["star"]}
            for r in rows
        ]

        return json.dumps(workspaces, default=str) if as_json else workspaces


    def get_star_workspaces(self, as_json=False):
        """Get all workspaces as list of dicts or JSON string."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT workspace_name, timestamp, star FROM workspace WHERE star = True;")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        # Since rows are dictionaries (RealDictCursor), use keys instead of indexes
        workspaces = [r["workspace_name"] for r in rows]

        return json.dumps(workspaces, default=str) if as_json else workspaces

    def flag_workspace(self, workspace_name):
        """
        Mark a workspace as starred (star = TRUE) given its workspace_name.
        Returns True if updated, False if workspace not found.
        """
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE workspace
            SET star = TRUE
            WHERE workspace_name = %s;
        """, (workspace_name,))

        updated_rows = cur.rowcount
        conn.commit()

        cur.close()
        conn.close()

    def undo_flag_workspace(self, workspace_name):
        """
        Mark a workspace as starred (star = False) given its workspace_name.
        Returns True if updated, False if workspace not found.
        """
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE workspace
            SET star = False
            WHERE workspace_name = %s;
        """, (workspace_name,))

        updated_rows = cur.rowcount
        conn.commit()

        cur.close()
        conn.close()

    def flag_status(self, workspace_name):
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT star
            FROM workspace
            WHERE workspace_name = %s;
        """, (workspace_name,))
        rows = cur.fetchone()
        cur.close()
        conn.close()
        return {"flag_status": rows["star"]}
    
    def toggle_workspace_flag(self, workspace_name: str):
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE workspace
            SET star = NOT star
            WHERE workspace_name = %s
            RETURNING star;
        """, (workspace_name,))

        result = cur.fetchone()
        conn.commit()

        cur.close()
        conn.close()

        if result is None:
            return None

        return result["star"]


    def delete_workspace(self, workspace_name: str):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM workspace WHERE workspace_name = %s;", (workspace_name,))
        conn.commit()
        cur.close()
        conn.close()
        print(f"🗑️ Workspace '{workspace_name}' deleted (cascade removed all related data).")


    # -------------------- PROCESSED FILES -------------------- #
    def save_processed_file(self, workspace_name, file_name, processed_text, uuid):
        """Save processed file data."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO processed_files (workspace_name, file_name, processed_text, uuid)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (uuid) DO NOTHING;
        """, (workspace_name, file_name, processed_text, uuid))
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ File '{file_name}' saved under workspace '{workspace_name}'.")


    def get_files_by_workspace(self, workspace_name, as_json=False):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT file_name, uuid
            FROM processed_files
            WHERE workspace_name = %s;
        """, (workspace_name,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        workspace_name = "".join(char for char in workspace_name if char in "0123456789")
        files = [{"file_name": r["file_name"], "uuid": r["uuid"], "workspace_name": workspace_name} for r in rows]
        return json.dumps(files, default=str) if as_json else files

    def get_processed_text_by_file_id(self, file_uuid, as_json=False):
        """Retrieve all files belonging to a workspace."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT processed_text, uuid
            FROM processed_files
            WHERE uuid = %s;
        """, (file_uuid,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        data = [{"processed_text": r["processed_text"], "uuid": r["uuid"]} for r in rows]
        return json.dumps(data, default=str) if as_json else data

    def get_processed_text_by_file_name(self, file_name, as_json=False):
        """Retrieve all files belonging to a workspace."""
        if isinstance(file_name, (tuple, list)):
            file_name = file_name[0]

        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT processed_text
            FROM processed_files
            WHERE file_name = %s;
        """, (file_name,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        data = [{"processed_text": r["processed_text"]} for r in rows]
        return json.dumps(data, default=str) if as_json else data

    def delete_file(self, file_uuid: str):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM processed_files WHERE uuid = %s;", (file_uuid,))
        conn.commit()
        cur.close()
        conn.close()
        print(f"🗑️ File with UUID '{file_uuid}' deleted (cascade removed related entities, relationships, embeddings).")
        

    def get_file_uuid_by_name(self, file_name: str, workspace_name: str = None):
        """
        Returns the UUID of a file by its name.
        Optionally, filter by workspace name to avoid ambiguity.
        """
        conn = self.get_connection()
        cur = conn.cursor()

        if workspace_name:
            cur.execute("""
                SELECT uuid FROM processed_files
                WHERE file_name = %s AND workspace_name = %s;
            """, (file_name, workspace_name))
        else:
            cur.execute("""
                SELECT uuid FROM processed_files
                WHERE file_name = %s;
            """, (file_name,))

        result = cur.fetchone()
        cur.close()
        conn.close()

        if result:
            # Handle both DictCursor and normal cursor
            if isinstance(result, dict):
                return result.get("uuid")
            else:
                return result[0]
        else:
            print(f"⚠️ No file found with name '{file_name}'"
                + (f" in workspace '{workspace_name}'." if workspace_name else "."))
            return None


    # -------------------- GRAPH ENTITY -------------------- #
    def save_graph_entity(self, uuid, name, type_, attributes, file_uuid):
        """Save an entity for a given file."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO graph_entity (uuid, name, type, attributes, file_uuid)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (uuid) DO NOTHING;
        """, (uuid, name, type_, Json(attributes), file_uuid))
        conn.commit()
        cur.close()
        conn.close()

    def get_entities_by_file(self, file_uuid, as_json=False):
        """Retrieve all entities for a given file UUID."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT name, type, attributes, uuid
            FROM graph_entity
            WHERE file_uuid = %s;
        """, (file_uuid,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        data = [{"name": r["name"], "type": r["type"], "attributes": r["attributes"], "uuid": r["uuid"]} for r in rows]
        return json.dumps(data, default=str) if as_json else data


    def get_entity_types_by_file(self, file_uuid):
        """Retrieve all DISTINCT entity types for a given file UUID."""
        
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT DISTINCT type
            FROM graph_entity
            WHERE file_uuid = %s;
        """, (file_uuid,))

        rows = cur.fetchall()

        cur.close()
        conn.close()

        # Flatten result: [(type1,), (type2,)] → [type1, type2]
        entity_types = [row["type"] for row in rows]
        return entity_types

    def get_entities_by_file_and_type(self, file_uuid, entity_type):
        """Retrieve all entity names for a given file UUID and entity type."""
        
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT name
            FROM graph_entity
            WHERE file_uuid = %s AND type = %s;
        """, (file_uuid, entity_type))

        rows = cur.fetchall()

        cur.close()
        conn.close()

        # ✅ Correct tuple unpacking
        data = [r["name"] for r in rows]

        return data

    # -------------------- GRAPH RELATIONSHIP -------------------- #
    def save_graph_relationship(self, uuid, relationship, description, file_uuid):
        """Save a relationship for a given file."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO graph_relationship (uuid, relationship, description, file_uuid)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (uuid) DO NOTHING;
        """, (uuid, Json(relationship), description, file_uuid))
        conn.commit()
        cur.close()
        conn.close()

    def normalize_rel(self, rel):
        if rel is None:
            return []

        if isinstance(rel, list):
            return rel

        if isinstance(rel, str):
            try:
                parsed = json.loads(rel)
                if isinstance(parsed, list):
                    return parsed
                if isinstance(parsed, dict):
                    return list(parsed.values())[:2]
            except:
                return []
            return []

        if isinstance(rel, dict):
            return list(rel.values())[:2]

        try:
            return list(rel)
        except:
            return []


    def get_relationships_by_file(self, file_uuid, as_json=False):
        conn = self.get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT uuid, relationship, description
            FROM graph_relationship
            WHERE file_uuid = %s;
        """, (file_uuid,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        for r in rows:
            r["relationship"] = self.normalize_rel(r.get("relationship"))

        return json.dumps(rows, default=str) if as_json else rows





    # -------------------- EMBEDDINGS -------------------- #
    def save_embedding(self, type_, reference_uuid, file_uuid, embedding):
        """Save an embedding (for an entity or relationship)."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO embeddings (type, reference_uuid, file_uuid, embedding)
            VALUES (%s, %s, %s, %s);
        """, (type_, reference_uuid, file_uuid, embedding))
        conn.commit()
        cur.close()
        conn.close()


    def get_embedding_by_reference(self, reference_uuid, as_json=False):
        """Retrieve embedding vector for a specific entity/relationship UUID."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT embedding
            FROM embeddings
            WHERE reference_uuid = %s;
        """, (reference_uuid,))
        result = cur.fetchall()
        cur.close()
        conn.close()
        return json.dumps(result, default=str) if as_json else result

    def get_embedding_by_file_uuid(self, file_uuid, as_json=False):
        """Retrieve embedding vector for a specific entity/relationship UUID."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM embeddings
            WHERE file_uuid = %s;
        """, (file_uuid,))
        result = cur.fetchall()
        cur.close()
        conn.close()
        return json.dumps(result, default=str) if as_json else result

    # ---------------------- CHAT SYSTEM -------------------------
    def add_memory(self, data: MemoryIn):
        conn = None
        cur = None

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            source_json = json.dumps(data.source) if data.source is not None else None
            filter_json = json.dumps(data.filter) if data.filter is not None else None

            embedding = (
                np.array(data.embedding, dtype=float)
                if data.embedding is not None else None
            )

            # ---- safety check for pgvector ----
            if embedding is not None and embedding.shape[0] != 768:
                raise ValueError(
                    f"Embedding dimension {embedding.shape[0]} != 768"
                )

            id_uuid = uuid.UUID(data.id)

            cur.execute("""
                INSERT INTO memory (
                    id, workspace, query, ai, source, embedding,
                    request_time, response_time, engine, filters
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET
                    workspace     = EXCLUDED.workspace,
                    query         = EXCLUDED.query,
                    ai            = EXCLUDED.ai,
                    source        = EXCLUDED.source,
                    embedding     = EXCLUDED.embedding,
                    request_time  = EXCLUDED.request_time,
                    response_time = EXCLUDED.response_time,
                    engine        = EXCLUDED.engine,
                    filters       = EXCLUDED.filters;
            """, (
                id_uuid,
                data.workspace,
                data.query,
                data.ai,
                source_json,
                embedding,
                data.request_time,
                data.response_time,
                data.engine,
                filter_json
            ))

            conn.commit()

            print(f"✅ memory saved | id={data.id} | query={data.query}")

        except Exception as e:
            if conn:
                conn.rollback()

            print("❌ ERROR while inserting memory")
            print("Type :", type(e).__name__)
            print("Error:", e)
            print("Traceback:")
            traceback.print_exc()

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()


    def add_query_memory(self, data: QueryIn):
        conn = self.get_connection()
        cur = conn.cursor()

        filters = data.filter

        # 🔥 NORMALIZATION STEP
        if isinstance(filters, list):
            filters = dict(filters)

        filters = json.dumps(filters) if filters is not None else None

        cur.execute("""
            INSERT INTO memory (id, workspace, query, engine, filters)
            VALUES (%s, %s, %s, %s, %s);
        """, (
            data.id,
            data.workspace,
            data.query,
            data.engine,
            filters
        ))

        conn.commit()
        cur.close()
        conn.close()

        print("✅ message added successfully in memory")

    
    def get_data_by_memory_id(self, memory_id: str):
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT workspace, query, engine, filters
            FROM memory
            WHERE id = %s;
            """,
            (memory_id,)
        )

        result = cur.fetchone()

        cur.close()
        conn.close()

        if result is None:
            return None  # or raise exception if you prefer

        return {
            "workspace": result["workspace"],
            "query": result["query"],
            "engine": result["engine"],
            "filter": result["filters"]
        }


    def get_memory(self, workspace_name: str, as_json=False):
        """Retrieve embedding vector for a specific entity/relationship UUID."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT query, ai, source, embedding, request_time, response_time
            FROM memory
            WHERE workspace = %s
            ORDER BY request_time ASC;
        """, (workspace_name,))
        result = cur.fetchall()
        cur.close()
        conn.close()
        return json.dumps(result, default=str) if as_json else result
    
    def get_memory_without_emb(self, workspace_name: str, as_json=False):
        """Retrieve embedding vector for a specific entity/relationship UUID."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, query, ai, source, request_time, response_time
            FROM memory
            WHERE workspace = %s
            ORDER BY request_time DESC;
        """, (workspace_name,))
        result = cur.fetchall()
        cur.close()
        conn.close()
        return json.dumps(result, default=str) if as_json else result
    
    def delete_last_n_memory(self, workspace_name: str, n: int) -> int:
        """
        Delete the latest N chat records for a workspace
        based on request_time.
        Returns number of rows deleted.
        """
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM memory
            WHERE id IN (
                SELECT id
                FROM memory
                WHERE workspace = %s
                ORDER BY request_time DESC
                LIMIT %s
            );
        """, (workspace_name, n))

        deleted = cur.rowcount
        conn.commit()

        cur.close()
        conn.close()

        return deleted
    
    def get_embedding64_by_file_uuid(self, file_uuid, as_json=False):
        """Retrieve embedding vector for a specific entity/relationship UUID."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM embedding64
            WHERE file_uuid = %s;
        """, (file_uuid,))
        result = cur.fetchall()
        cur.close()
        conn.close()
        return json.dumps(result, default=str) if as_json else result

    def save_embedding64(self, type_, reference_uuid, file_uuid, embedding):
        """Save an embedding (for an entity or relationship)."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO embedding64 (type, reference_uuid, file_uuid, embedding)
            VALUES (%s, %s, %s, %s);
        """, (type_, reference_uuid, file_uuid, embedding))
        conn.commit()
        cur.close()
        conn.close()

    def delete_all_embeddings64(self):
        """Delete all rows from embedding64 table."""
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("DELETE FROM embedding64;")

        conn.commit()
        cur.close()
        conn.close()

    def delete_all_embeddings(self):
        """Delete all rows from embedding64 table."""
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("DELETE FROM embedding;")

        conn.commit()
        cur.close()
        conn.close()

    def delete_empty_ai_memory(self):
        """Delete all memory rows where AI response is empty (NULL, '', or whitespace)."""
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM memory
            WHERE ai IS NULL
            OR BTRIM(ai) = '';
        """)

        deleted = cur.rowcount

        conn.commit()
        cur.close()
        conn.close()

        return deleted

    def get_starred_workspace_embeddings(self):
        """
        Fetch text + embedding for all workspaces where star = true.
        No normalization is applied.
        Returns: List[Tuple[text, embedding]]
        """
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT
            COALESCE(
                CASE
                WHEN ge.uuid IS NOT NULL THEN
                    'name ' || ge.name ||
                    ' type ' || ge.type ||
                    ' attributes ' || COALESCE(ge.attributes::text, '') ||
                    ' source ' || pf.file_name
                WHEN gr.uuid IS NOT NULL THEN
                    'relationship ' || COALESCE(gr.relationship::text,'') ||
                    ' description ' || COALESCE(gr.description,'') ||
                    ' source ' || pf.file_name
                ELSE
                    COALESCE(pf.processed_text,'')
                END,
                ''
            ) AS text,
            e.embedding
            FROM workspace w
            JOIN processed_files pf
            ON pf.workspace_name = w.workspace_name
            JOIN embedding64 e
            ON e.file_uuid = pf.uuid
            LEFT JOIN graph_entity ge
            ON ge.uuid = e.reference_uuid
            LEFT JOIN graph_relationship gr
            ON gr.uuid = e.reference_uuid
            WHERE w.star = true;
        """)

        rows = cur.fetchall()

        cur.close()
        conn.close()

        return rows
    
    def get_workspace_embeddings(self, workspace_name: str):
        """
        Fetch text + embedding for a specific workspace (flag ignored).
        No normalization is applied.
        Returns: List[Tuple[text, embedding]]
        """
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT
            COALESCE(
                CASE
                WHEN ge.uuid IS NOT NULL THEN
                    'name ' || ge.name ||
                    ' type ' || ge.type ||
                    ' attributes ' || COALESCE(ge.attributes::text, '') ||
                    ' source ' || pf.file_name
                WHEN gr.uuid IS NOT NULL THEN
                    'relationship ' || COALESCE(gr.relationship::text,'') ||
                    ' description ' || COALESCE(gr.description,'') ||
                    ' source ' || pf.file_name
                ELSE
                    COALESCE(pf.processed_text,'')
                END,
                ''
            ) AS text,
            e.embedding
            FROM processed_files pf
            JOIN embedding64 e
            ON e.file_uuid = pf.uuid
            LEFT JOIN graph_entity ge
            ON ge.uuid = e.reference_uuid
            LEFT JOIN graph_relationship gr
            ON gr.uuid = e.reference_uuid
            WHERE pf.workspace_name = %s;
        """, (workspace_name,))

        rows = cur.fetchall()

        cur.close()
        conn.close()

        return rows

    def get_entity_types_by_workspace(self, workspace_name: str, as_json: bool = False):
        """
        Retrieve all DISTINCT entity types present in a given workspace.
        Traverses:
        workspace -> processed_files -> graph_entity

        Returns:
            List[str] or JSON string
        """
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT DISTINCT ge.type
            FROM graph_entity ge
            JOIN processed_files pf
                ON ge.file_uuid = pf.uuid
            WHERE pf.workspace_name = %s;
        """, (workspace_name,))

        rows = cur.fetchall()

        cur.close()
        conn.close()

        entity_types = [r["type"] for r in rows]

        return json.dumps(entity_types) if as_json else entity_types
