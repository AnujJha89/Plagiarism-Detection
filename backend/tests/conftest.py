import io
import pytest
from pathlib import Path
from docx import Document

# Override settings to use a test database and index BEFORE importing app or other components
from app.config import settings
settings.POSTGRES_DB = "test_lemma"
settings.CELERY_ALWAYS_EAGER = True
settings.ENABLE_ONLINE_RETRIEVAL = False

from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture(scope="session", autouse=True)
def clean_test_db_and_index():
    """Ensures test database tables and FAISS index are initialized and cleaned up."""
    from app.services.database import DatabaseService
    
    try:
        DatabaseService.initialize_db()
        DatabaseService.clear_db()
    except Exception as e:
        pytest.skip(f"Database initialization failed: {e}")
        
    yield
    
    try:
        DatabaseService.clear_db()
    except Exception:
        pass

@pytest.fixture(scope="module")
def client():
    """Provides a FastAPI TestClient."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_text():
    """Provides a standard multi-sentence plain text string."""
    return (
        "This is the first sentence. It has some text. "
        "Here is the second sentence, which is longer and contains more details! "
        "And this is the third sentence: does it work correctly?"
    )

@pytest.fixture
def create_docx_bytes():
    """Fixture that returns a function to generate DOCX bytes on-the-fly."""
    def _create(paragraphs: list[str]) -> bytes:
        doc = Document()
        for p in paragraphs:
            doc.add_paragraph(p)
        
        doc_io = io.BytesIO()
        doc.save(doc_io)
        return doc_io.getvalue()
    return _create
