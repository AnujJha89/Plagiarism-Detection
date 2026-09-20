import os
import sys
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Try to discover and add GTK3/Pango DLL directories on Windows before importing WeasyPrint
if sys.platform == "win32":
    # Common GTK3/Pango binary directory paths on Windows
    gtk_paths = [
        r"C:\Program Files\GTK3-Runtime Win64\bin",
        r"C:\Program Files (x86)\GTK3-Runtime Win64\bin",
        r"C:\msys64\mingw64\bin",
        r"C:\msys64\ucrt64\bin",
        # Local workspace paths
        str(Path(__file__).resolve().parent.parent / "gtk" / "bin")
    ]
    
    # Check GTK3_PATH or PATH env variables
    env_gtk_path = os.environ.get("GTK3_PATH")
    if env_gtk_path:
        gtk_paths.append(os.path.join(env_gtk_path, "bin"))
        gtk_paths.append(env_gtk_path)
        
    for path in gtk_paths:
        if os.path.isdir(path):
            if any(os.path.exists(os.path.join(path, name)) for name in ["libgobject-2.0-0.dll", "gobject-2.0-0.dll"]):
                try:
                    os.add_dll_directory(path)
                except AttributeError:
                    os.environ["PATH"] = path + os.pathsep + os.environ["PATH"]
                break


class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Lemma Plagiarism Analysis Platform"
    
    # Path configuration
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    
    # File limits
    MAX_FILE_SIZE_MB: int = 100
    ALLOWED_EXTENSIONS: set[str] = {"pdf", "docx", "txt"}
    
    # NLP / AI Settings
    SPACY_MODEL: str = "en_core_web_sm"
    SENTENCE_TRANSFORMERS_MODEL: str = "all-MiniLM-L6-v2"
    MOCK_DATABASE_PATH: Path = BASE_DIR / "data" / "mock_references.json"
    LEXICAL_THRESHOLD: float = 0.70
    SEMANTIC_THRESHOLD: float = 0.65
    HYBRID_THRESHOLD: float = 0.60
    
    # Database Settings
    DATABASE_URL: str | None = None
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "lemma"
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""
    
    # Elasticsearch Settings
    ELASTICSEARCH_URL: str = "http://localhost:9200"
    
    # Online Retrieval Settings
    ENABLE_ONLINE_RETRIEVAL: bool = False
    SEMANTIC_SCHOLAR_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    MAX_ONLINE_CANDIDATES_PER_QUERY: int = 30
    
    # Deprecated/Fallback Settings
    SQLITE_DB_FILE: str = "lemma.db"
    SQLITE_DB_PATH: Path = BASE_DIR / "data" / "lemma.db"
    FAISS_INDEX_PATH: Path = BASE_DIR / "data" / "lemma_vectors.index"
    
    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_ALWAYS_EAGER: bool = True
    
    # Ollama settings
    OLLAMA_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "lemma-model"
    
    # Firebase settings (to be integrated fully in Phase 4)
    FIREBASE_CREDENTIALS_PATH: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        env_prefix="LEMMA_"
    )

settings = Settings()

# Ensure uploads and data directories exist
try:
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    import tempfile
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.warning(f"Could not create UPLOAD_DIR at {settings.UPLOAD_DIR}: {e}. Falling back to system temp directory.")
    settings.UPLOAD_DIR = Path(tempfile.gettempdir()) / "lemma_uploads"
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

try:
    settings.FAISS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
except Exception as e:
    import tempfile
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"Could not create FAISS parent directory at {settings.FAISS_INDEX_PATH.parent}: {e}. Falling back to system temp directory.")
    settings.FAISS_INDEX_PATH = Path(tempfile.gettempdir()) / "lemma_vectors.index"
