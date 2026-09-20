"""
Elasticsearch shim — runs entirely in-memory using scikit-learn TF-IDF.
Drop-in replacement so the app works without a running Elasticsearch instance.
"""
import logging
import difflib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

logger = logging.getLogger(__name__)

# In-memory store: list of {"document_id", "sentence_index", "text"}
_sentence_store: list[dict] = []
_vectorizer: TfidfVectorizer | None = None
_tfidf_matrix = None


def _rebuild_index():
    global _vectorizer, _tfidf_matrix
    if not _sentence_store:
        _vectorizer = None
        _tfidf_matrix = None
        return
    _vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=50000)
    corpus = [s["text"] for s in _sentence_store]
    _tfidf_matrix = _vectorizer.fit_transform(corpus)
    logger.info(f"[ES-Shim] TF-IDF index rebuilt with {len(corpus)} sentences.")


def get_es_client():
    """No-op: returns None — shim does not use a real ES client."""
    return None


def initialize_es() -> None:
    """No-op: index is always ready (in-memory)."""
    logger.info("[ES-Shim] initialize_es() called — using in-memory TF-IDF index.")


def index_sentence_bulk(sentences: list[dict]) -> None:
    """Adds sentences to the in-memory TF-IDF store and rebuilds the index."""
    global _sentence_store
    existing_keys = {(s["document_id"], s["sentence_index"]) for s in _sentence_store}
    new_sentences = [
        s for s in sentences
        if (s["document_id"], s["sentence_index"]) not in existing_keys
    ]
    if not new_sentences:
        logger.info("[ES-Shim] No new sentences to index.")
        return
    _sentence_store.extend(new_sentences)
    _rebuild_index()
    logger.info(f"[ES-Shim] Indexed {len(new_sentences)} new sentences. Total: {len(_sentence_store)}")


def search_sentences_bm25(query_text: str, k: int = 20, job_id: str = None) -> list[dict]:
    """
    Performs TF-IDF cosine similarity search against the in-memory sentence store.
    Falls back to difflib sequence matching if the index is empty.
    """
    if not query_text.strip():
        return []

    if _vectorizer is None or _tfidf_matrix is None or len(_sentence_store) == 0:
        logger.warning("[ES-Shim] Index is empty. Returning empty results.")
        return []

    # Filter by job_id prefix if provided
    if job_id:
        indices = [
            i for i, s in enumerate(_sentence_store)
            if s["document_id"].startswith("ref_") or s["document_id"].startswith(f"job_{job_id}_")
        ]
    else:
        indices = list(range(len(_sentence_store)))

    if not indices:
        return []

    try:
        query_vec = _vectorizer.transform([query_text])
        subset_matrix = _tfidf_matrix[indices]
        scores = cosine_similarity(query_vec, subset_matrix).flatten()

        top_local_indices = np.argsort(scores)[::-1][:k]
        results = []
        for local_idx in top_local_indices:
            global_idx = indices[local_idx]
            score = float(scores[local_idx])
            if score > 0:
                s = _sentence_store[global_idx]
                results.append({
                    "document_id": s["document_id"],
                    "sentence_index": s["sentence_index"],
                    "text": s["text"],
                    "score": score
                })
        return results
    except Exception as e:
        logger.error(f"[ES-Shim] TF-IDF search failed: {e}")
        return []
