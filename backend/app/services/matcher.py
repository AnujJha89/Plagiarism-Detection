"""
Dual-tier plagiarism matcher using local SQLite + FAISS + TF-IDF.
Drop-in replacement for the PostgreSQL + pgvector version — no Docker needed.
"""
import json
import difflib
import logging
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer

from app.config import settings
from app.services.segmenter import SentenceSegmenterService
from app.services.database import DatabaseService
from app.services.elasticsearch_client import (
    initialize_es,
    index_sentence_bulk,
    search_sentences_bm25,
)

logger = logging.getLogger(__name__)

# Optional FAISS import — gracefully degrade if unavailable
try:
    import faiss
    _FAISS_AVAILABLE = True
except ImportError:
    _FAISS_AVAILABLE = False
    logger.warning("faiss-cpu not available — semantic search disabled.")


# ──────────────────────────────────────────────
# FAISS index helpers
# ──────────────────────────────────────────────

_faiss_index = None        # faiss.IndexFlatIP
_faiss_id_map: list[int] = []  # maps faiss position → sentences.id (SQLite PK)


def _build_faiss_index(rows: list[dict]) -> None:
    """Builds an in-memory FAISS index from DB rows that have embeddings."""
    global _faiss_index, _faiss_id_map
    if not _FAISS_AVAILABLE:
        return

    vecs, ids = [], []
    for row in rows:
        if row.get("embedding"):
            vecs.append(row["embedding"])
            ids.append(row["id"])

    if not vecs:
        _faiss_index = None
        _faiss_id_map = []
        return

    dim = len(vecs[0])
    matrix = np.array(vecs, dtype="float32")
    # L2-normalize for cosine similarity via inner product
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix /= norms

    index = faiss.IndexFlatIP(dim)
    index.add(matrix)
    _faiss_index = index
    _faiss_id_map = ids
    logger.info(f"[Matcher] FAISS index built with {len(ids)} vectors (dim={dim}).")


def search_sentences_semantic(query_vector: list[float], k: int = 20, job_id: str = None) -> list[dict]:
    """
    Cosine similarity search against the in-memory FAISS index.
    Falls back to empty list if FAISS is unavailable or index is empty.
    """
    if not _FAISS_AVAILABLE or _faiss_index is None or len(_faiss_id_map) == 0:
        return []

    vec = np.array([query_vector], dtype="float32")
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm

    actual_k = min(k, _faiss_index.ntotal)
    scores, positions = _faiss_index.search(vec, actual_k)

    # Fetch sentence metadata from SQLite
    results = []
    with DatabaseService.get_connection() as conn:
        for score, pos in zip(scores[0], positions[0]):
            if pos < 0:
                continue
            sentence_id = _faiss_id_map[pos]
            row = conn.execute("""
                SELECT s.text, s.document_id, s.sentence_index, d.title, d.author, d.source
                FROM sentences s JOIN documents d ON s.document_id = d.id
                WHERE s.id = ?;
            """, (sentence_id,)).fetchone()
            if row is None:
                continue
            doc_id = row["document_id"]
            # Filter by job_id if provided
            if job_id and not (doc_id.startswith("ref_") or doc_id.startswith(f"job_{job_id}_")):
                continue
            results.append({
                "document_id": doc_id,
                "sentence_index": row["sentence_index"],
                "text": row["text"],
                "title": row["title"],
                "author": row["author"],
                "source": row["source"],
                "score": float(score),
            })
    return results


# ──────────────────────────────────────────────
# Seeding
# ──────────────────────────────────────────────

def seed_database() -> None:
    """Seeds SQLite + TF-IDF + FAISS from the mock JSON references file if empty."""
    DatabaseService.initialize_db()

    try:
        count = DatabaseService.get_sentence_count()
        if count > 0:
            logger.info("[Matcher] Database already seeded — loading FAISS index.")
            rows = DatabaseService.get_all_sentences_with_embeddings()
            _build_faiss_index(rows)
            # Re-populate ES shim
            index_sentence_bulk([
                {"document_id": r["document_id"], "sentence_index": r["sentence_index"], "text": r["text"]}
                for r in rows
            ])
            return
    except Exception as e:
        logger.error(f"[Matcher] Could not check sentence count: {e}")

    mock_path = Path(settings.MOCK_DATABASE_PATH)
    if not mock_path.exists():
        logger.warning(f"[Matcher] Mock references file not found at: {mock_path}")
        return

    with open(mock_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    initialize_es()

    flat_sentences = []
    for doc in data:
        DatabaseService.insert_reference_document(
            doc_id=doc["id"],
            title=doc["title"],
            author=doc["author"],
            source=doc["source"],
        )
        sentences = SentenceSegmenterService.segment(doc["text"])
        for idx, s in enumerate(sentences):
            flat_sentences.append({
                "document_id": doc["id"],
                "sentence_index": idx,
                "text": s["text"],
            })

    if not flat_sentences:
        return

    # Generate vector embeddings
    model = SemanticMatcher.get_model()
    corpus = [s["text"] for s in flat_sentences]
    embeddings = model.encode(corpus, show_progress_bar=True)
    for s, emb in zip(flat_sentences, embeddings):
        s["embedding"] = emb.tolist()

    # Persist to SQLite
    DatabaseService.insert_reference_sentences(flat_sentences)

    # Build FAISS index
    rows = DatabaseService.get_all_sentences_with_embeddings()
    _build_faiss_index(rows)

    # Populate TF-IDF shim
    index_sentence_bulk(flat_sentences)
    logger.info("[Matcher] Database, FAISS, and TF-IDF index seeded successfully.")


def load_references() -> list[dict]:
    """Loads all references from SQLite. Seeds the database if it is empty."""
    DatabaseService.initialize_db()
    try:
        count = DatabaseService.get_sentence_count()
        if count == 0:
            seed_database()
    except Exception:
        seed_database()

    rows = DatabaseService.get_all_sentences_with_embeddings()
    return [
        {
            "text": r["text"],
            "faiss_id": r["id"],
            "doc_id": r["document_id"],
            "doc_title": r["title"],
            "doc_author": r["author"],
            "doc_source": r["source"],
        }
        for r in rows
    ]


# ──────────────────────────────────────────────
# Matchers
# ──────────────────────────────────────────────

class LexicalMatcher:
    """Detects verbatim or near-verbatim copy-paste text using TF-IDF BM25 shim."""

    def __init__(self, references: list[dict] = None):
        self.references = references

    def find_match(self, query_text: str, threshold: float = None, job_id: str = None) -> dict | None:
        if threshold is None:
            threshold = settings.LEXICAL_THRESHOLD

        results = search_sentences_bm25(query_text, k=1, job_id=job_id)
        if not results:
            return None

        best = results[0]
        q_words = query_text.lower().split()
        r_words = best["text"].lower().split()
        similarity = difflib.SequenceMatcher(None, q_words, r_words).ratio()

        if similarity >= threshold:
            # Look up document metadata from SQLite
            with DatabaseService.get_connection() as conn:
                row = conn.execute(
                    "SELECT title, author, source FROM documents WHERE id = ?;",
                    (best["document_id"],)
                ).fetchone()

            doc_title = row["title"] if row else "Unknown"
            doc_author = row["author"] if row else "N/A"
            doc_source = row["source"] if row else "N/A"

            return {
                "score": similarity,
                "text": best["text"],
                "doc_id": best["document_id"],
                "doc_title": doc_title,
                "doc_author": doc_author,
                "doc_source": doc_source,
            }
        return None


class SemanticMatcher:
    """Detects paraphrased or structurally modified sentences using local FAISS."""

    _model: SentenceTransformer | None = None

    @classmethod
    def get_model(cls) -> SentenceTransformer:
        if cls._model is None:
            cls._model = SentenceTransformer(settings.SENTENCE_TRANSFORMERS_MODEL)
        return cls._model

    def __init__(self, references: list[dict] = None):
        self.references = references

    def find_match(self, query_text: str, threshold: float = None, job_id: str = None) -> dict | None:
        if threshold is None:
            threshold = settings.SEMANTIC_THRESHOLD

        model = self.get_model()
        query_embedding = model.encode(query_text, show_progress_bar=False).tolist()
        results = search_sentences_semantic(query_embedding, k=1, job_id=job_id)

        if not results:
            return None

        best = results[0]
        if best["score"] >= threshold:
            return {
                "score": best["score"],
                "text": best["text"],
                "doc_id": best["document_id"],
                "doc_title": best["title"],
                "doc_author": best["author"],
                "doc_source": best["source"],
            }
        return None


# ──────────────────────────────────────────────
# Utility: get matching character slices
# ──────────────────────────────────────────────

def get_matching_slices(query: str, ref: str, min_length: int = 6) -> list[dict]:
    """Extracts matching character slices between query and reference sentences."""
    matcher = difflib.SequenceMatcher(None, query.lower(), ref.lower())
    matching_blocks = matcher.get_matching_blocks()

    slices = []
    for block in matching_blocks:
        start_q, _, size = block
        if size >= min_length:
            matched_text = query[start_q: start_q + size]
            stripped_text = matched_text.strip()
            if stripped_text:
                leading = len(matched_text) - len(matched_text.lstrip())
                trailing = len(matched_text) - len(matched_text.rstrip())
                final_start = start_q + leading
                final_end = start_q + size - trailing
                if (final_end - final_start) >= min_length:
                    slices.append({
                        "start": final_start,
                        "end": final_end,
                        "text": query[final_start:final_end],
                    })

    if not slices:
        return []

    slices.sort(key=lambda x: x["start"])
    merged = [slices[0]]
    for current in slices[1:]:
        prev = merged[-1]
        if current["start"] <= prev["end"] + 2:
            prev["end"] = max(prev["end"], current["end"])
            prev["text"] = query[prev["start"]: prev["end"]]
        else:
            merged.append(current)
    return merged


# ──────────────────────────────────────────────
# DualTierMatcher
# ──────────────────────────────────────────────

class DualTierMatcher:
    """Coordinates lexical (TF-IDF) and semantic (FAISS) matching stages using RRF."""

    def __init__(self, references: list[dict] = None):
        seed_database()
        self.lexical_matcher = LexicalMatcher(references)
        self.semantic_matcher = SemanticMatcher(references)

    def analyze_sentence(
        self,
        query_sentence: str,
        lexical_threshold: float = None,
        semantic_threshold: float = None,
        job_id: str = None,
    ) -> dict | None:
        """Runs hybrid plagiarism analysis using RRF across TF-IDF and FAISS."""
        if lexical_threshold is None:
            lexical_threshold = settings.LEXICAL_THRESHOLD
        if semantic_threshold is None:
            semantic_threshold = settings.SEMANTIC_THRESHOLD

        # Query A: TF-IDF BM25 shim
        es_results = search_sentences_bm25(query_sentence, k=20, job_id=job_id)

        # Query B: FAISS semantic
        model = SemanticMatcher.get_model()
        query_embedding = model.encode(query_sentence, show_progress_bar=False).tolist()
        semantic_results = search_sentences_semantic(query_embedding, k=20, job_id=job_id)

        if not es_results and not semantic_results:
            return None

        # Reciprocal Rank Fusion
        k_rrf = 60
        candidates: dict = {}

        for rank_idx, res in enumerate(es_results):
            key = (res["document_id"], res["sentence_index"])
            rank = rank_idx + 1
            candidates[key] = {
                "document_id": res["document_id"],
                "sentence_index": res["sentence_index"],
                "text": res["text"],
                "es_rank": rank,
                "es_score": res["score"],
                "semantic_rank": None,
                "semantic_score": None,
                "rrf_score": 1.0 / (k_rrf + rank),
            }

        for rank_idx, res in enumerate(semantic_results):
            key = (res["document_id"], res["sentence_index"])
            rank = rank_idx + 1
            if key in candidates:
                candidates[key]["semantic_rank"] = rank
                candidates[key]["semantic_score"] = res["score"]
                candidates[key]["rrf_score"] += 1.0 / (k_rrf + rank)
                candidates[key].update({
                    "text": res["text"],
                    "title": res["title"],
                    "author": res["author"],
                    "source": res["source"],
                })
            else:
                candidates[key] = {
                    "document_id": res["document_id"],
                    "sentence_index": res["sentence_index"],
                    "text": res["text"],
                    "es_rank": None,
                    "es_score": None,
                    "semantic_rank": rank,
                    "semantic_score": res["score"],
                    "title": res["title"],
                    "author": res["author"],
                    "source": res["source"],
                    "rrf_score": 1.0 / (k_rrf + rank),
                }

        # Resolve missing doc metadata from SQLite for ES-only candidates
        missing_doc_ids = [key[0] for key, c in candidates.items() if "title" not in c]
        if missing_doc_ids:
            with DatabaseService.get_connection() as conn:
                for doc_id in missing_doc_ids:
                    row = conn.execute(
                        "SELECT title, author, source FROM documents WHERE id = ?;", (doc_id,)
                    ).fetchone()
                    for key, c in candidates.items():
                        if key[0] == doc_id and "title" not in c:
                            c["title"] = row["title"] if row else "Unknown Reference"
                            c["author"] = row["author"] if row else "N/A"
                            c["source"] = row["source"] if row else "N/A"

        sorted_candidates = sorted(candidates.values(), key=lambda x: x["rrf_score"], reverse=True)

        if not sorted_candidates:
            return None

        best = sorted_candidates[0]
        rrf_max = 2.0 / (k_rrf + 1)
        normalized_rrf = best["rrf_score"] / rrf_max

        q_words = query_sentence.lower().split()
        r_words = best["text"].lower().split()
        lexical_sim = difflib.SequenceMatcher(None, q_words, r_words).ratio()

        # Classify match type
        if best["es_rank"] is not None and best["semantic_rank"] is not None:
            match_type = "hybrid"
        elif best["es_rank"] is not None:
            match_type = "lexical"
        else:
            match_type = "semantic"

        # Validate thresholds
        is_valid = False
        if match_type == "hybrid":
            if normalized_rrf >= settings.HYBRID_THRESHOLD:
                if (best["semantic_score"] is not None and best["semantic_score"] >= semantic_threshold) or (
                    lexical_sim >= lexical_threshold
                ):
                    is_valid = True
        elif match_type == "lexical":
            if lexical_sim >= lexical_threshold:
                is_valid = True
        elif match_type == "semantic":
            if best["semantic_score"] is not None and best["semantic_score"] >= semantic_threshold:
                is_valid = True

        if not is_valid:
            return None

        display_score = best["semantic_score"] if best["semantic_score"] is not None else lexical_sim
        display_score = max(0.0, min(1.0, display_score))

        return {
            "score": display_score,
            "text": best["text"],
            "doc_id": best["document_id"],
            "doc_title": best.get("title", "Unknown"),
            "doc_author": best.get("author", "N/A"),
            "doc_source": best.get("source", "N/A"),
            "match_type": match_type,
            "normalized_rrf": normalized_rrf,
        }

    def analyze_document(
        self,
        sentences: list[dict],
        lexical_threshold: float = None,
        semantic_threshold: float = None,
        job_id: str = None,
    ) -> dict:
        """Performs full document plagiarism analysis across segmented sentence coordinate structures."""
        matched_sentences = []
        lexical_count = semantic_count = hybrid_count = 0

        for s in sentences:
            q_text = s["text"]
            match = self.analyze_sentence(q_text, lexical_threshold, semantic_threshold, job_id=job_id)

            if match:
                if match["match_type"] == "lexical":
                    lexical_count += 1
                elif match["match_type"] == "semantic":
                    semantic_count += 1
                elif match["match_type"] == "hybrid":
                    hybrid_count += 1

                slices = get_matching_slices(q_text, match["text"])
                abs_highlights = [
                    {
                        "start_char": s["start_char"] + sl["start"],
                        "end_char": s["start_char"] + sl["end"],
                        "text": sl["text"],
                    }
                    for sl in slices
                ]

                matched_sentences.append({
                    "query_sentence": {
                        "text": q_text,
                        "start_char": s["start_char"],
                        "end_char": s["end_char"],
                    },
                    "matched_sentence": {
                        "text": match["text"],
                        "doc_id": match["doc_id"],
                        "doc_title": match["doc_title"],
                        "doc_author": match["doc_author"],
                        "doc_source": match["doc_source"],
                    },
                    "match_type": match["match_type"],
                    "score": match["score"],
                    "highlights": abs_highlights,
                })

        total = len(sentences)
        plagiarized = len(matched_sentences)
        return {
            "plagiarism_score": (plagiarized / total) if total > 0 else 0.0,
            "total_sentences": total,
            "plagiarized_sentences_count": plagiarized,
            "lexical_matches_count": lexical_count,
            "semantic_matches_count": semantic_count,
            "hybrid_matches_count": hybrid_count,
            "matches": matched_sentences,
        }
