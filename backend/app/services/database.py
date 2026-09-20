"""
SQLite-backed DatabaseService — drop-in replacement for the PostgreSQL version.
Stores document metadata and sentence embeddings locally with no external dependencies.
"""
import json
import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from app.config import settings

logger = logging.getLogger(__name__)

# SQLite database file path (sibling to the FAISS index)
_DB_PATH: Path = settings.FAISS_INDEX_PATH.parent / "lemma.db"


@contextmanager
def _sqlite_connection():
    """Context manager that yields a sqlite3 connection and commits/closes automatically."""
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class DatabaseService:
    """Manages SQLite database connections, table creation, and metadata queries."""

    @staticmethod
    @contextmanager
    def get_connection():
        """Returns a context-managed SQLite connection (mimics psycopg2 API)."""
        with _sqlite_connection() as conn:
            yield conn

    @classmethod
    def initialize_db(cls) -> None:
        """Creates the SQLite tables if they do not already exist."""
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _sqlite_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    author TEXT,
                    source TEXT
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sentences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    sentence_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding TEXT,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
            """)
        logger.info(f"[DB-SQLite] Database initialized at {_DB_PATH}")

    @classmethod
    def clear_db(cls) -> None:
        """Clears all records from the tables (useful for tests)."""
        with _sqlite_connection() as conn:
            conn.execute("DELETE FROM sentences;")
            conn.execute("DELETE FROM documents;")

    @classmethod
    def get_sentence_count(cls) -> int:
        """Returns the total number of sentences in the database."""
        with _sqlite_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM sentences;").fetchone()
            return row[0] if row else 0

    @classmethod
    def insert_reference_document(cls, doc_id: str, title: str, author: str, source: str) -> None:
        """Inserts a document metadata record into the database."""
        with _sqlite_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO documents (id, title, author, source)
                   VALUES (?, ?, ?, ?);""",
                (doc_id, title, author, source)
            )

    @classmethod
    def insert_reference_sentences(cls, sentences: list[dict]) -> None:
        """
        Bulk inserts sentences into the database.
        Each dict must contain: document_id, sentence_index, text, embedding (optional list[float]).
        """
        with _sqlite_connection() as conn:
            data = [
                (
                    s["document_id"],
                    s["sentence_index"],
                    s["text"],
                    json.dumps(s["embedding"]) if s.get("embedding") is not None else None
                )
                for s in sentences
            ]
            conn.executemany(
                """INSERT OR IGNORE INTO sentences (document_id, sentence_index, text, embedding)
                   VALUES (?, ?, ?, ?);""",
                data
            )

    @classmethod
    def get_sentence_by_faiss_id(cls, sentence_id: int) -> dict | None:
        """Retrieves a sentence and its associated document metadata by its primary key ID."""
        with _sqlite_connection() as conn:
            row = conn.execute("""
                SELECT s.text AS sentence_text, s.document_id, d.title, d.author, d.source
                FROM sentences s
                JOIN documents d ON s.document_id = d.id
                WHERE s.id = ?;
            """, (sentence_id,)).fetchone()
            if row:
                return {
                    "text": row["sentence_text"],
                    "doc_id": row["document_id"],
                    "doc_title": row["title"],
                    "doc_author": row["author"],
                    "doc_source": row["source"]
                }
            return None

    @classmethod
    def get_all_sentences_with_embeddings(cls, job_id: str = None) -> list[dict]:
        """Returns all sentences (optionally filtered by job_id prefix) with their embeddings."""
        with _sqlite_connection() as conn:
            if job_id:
                rows = conn.execute("""
                    SELECT s.id, s.document_id, s.sentence_index, s.text, s.embedding,
                           d.title, d.author, d.source
                    FROM sentences s
                    JOIN documents d ON s.document_id = d.id
                    WHERE s.document_id LIKE 'ref_%' OR s.document_id LIKE ?
                    ORDER BY s.id ASC;
                """, (f"job_{job_id}_%",)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT s.id, s.document_id, s.sentence_index, s.text, s.embedding,
                           d.title, d.author, d.source
                    FROM sentences s
                    JOIN documents d ON s.document_id = d.id
                    ORDER BY s.id ASC;
                """).fetchall()

            results = []
            for row in rows:
                results.append({
                    "id": row["id"],
                    "document_id": row["document_id"],
                    "sentence_index": row["sentence_index"],
                    "text": row["text"],
                    "embedding": json.loads(row["embedding"]) if row["embedding"] else None,
                    "title": row["title"],
                    "author": row["author"],
                    "source": row["source"]
                })
            return results
