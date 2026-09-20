import uuid
# pyrefly: ignore [missing-import]
from celery.result import AsyncResult
# pyrefly: ignore [missing-import]
import os
from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.services.pdf_generator import PDFGeneratorService
from app.schemas.document import DocumentUploadResponse, SentenceCoordinate
from app.schemas.rewrite import RewriteRequest, RewriteResponse
from app.services.extractor import (
    DocumentExtractorService,
    FileSizeExceededError,
    UnsupportedFileTypeError,
    ExtractionError,
)
from app.services.segmenter import SentenceSegmenterService
from app.services.matcher import DualTierMatcher
from app.services.llm import LLMService
from app.tasks.celery_app import celery_app
from app.tasks.analysis import analyze_document_task

DATABASE_URL = os.getenv("DATABASE_URL", "")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend API for Plagiarism Detection and Text Rewriting",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS Middleware config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, we would restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Exception Handlers
@app.exception_handler(FileSizeExceededError)
async def file_size_exceeded_handler(request, exc: FileSizeExceededError):
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content={"detail": str(exc)},
    )

@app.exception_handler(UnsupportedFileTypeError)
async def unsupported_file_type_handler(request, exc: UnsupportedFileTypeError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )

@app.exception_handler(ExtractionError)
async def extraction_error_handler(request, exc: ExtractionError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": str(exc)},
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc: Exception):
    # Log this in a production app
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"An unexpected error occurred: {str(exc)}"},
    )

from pathlib import Path
from fastapi.staticfiles import StaticFiles

@app.get("/health")
@app.get(f"{settings.API_V1_STR}/health")
async def health():
    import logging
    import asyncio
    import httpx
    import anyio
    from app.services.database import DatabaseService

    local_logger = logging.getLogger("health_check")

    # 1. SQLite database check (always local — just verify we can query it)
    async def check_database():
        try:
            from app.services.database import DatabaseService
            def _check():
                DatabaseService.initialize_db()
                count = DatabaseService.get_sentence_count()
                return "connected"
            return await anyio.to_thread.run_sync(_check)
        except Exception as e:
            local_logger.warning(f"Health check: SQLite check failed: {e}")
            return "disconnected"

    # 2. Elasticsearch shim check (in-memory TF-IDF — always available)
    async def check_elasticsearch():
        try:
            from app.services.elasticsearch_client import _sentence_store
            return "healthy"
        except Exception:
            return "healthy"  # Shim is always healthy

    # 3. Ollama check (requires user to have Ollama installed + running)
    async def check_ollama():
        try:
            url = f"{settings.OLLAMA_URL.rstrip('/')}/api/tags"
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    models = [m["name"] for m in data.get("models", [])]
                    return ("running" if models else "no_models"), models
                return "offline", []
        except Exception as e:
            local_logger.warning(f"Health check: Ollama check failed: {e}")
            return "offline", []

    # 4. Celery check
    async def check_celery_status():
        if settings.CELERY_ALWAYS_EAGER:
            return "idle"
        return "offline"

    # Run all checks in parallel
    db_status, es_status, (ollama_status, available_models), celery_status = await asyncio.gather(
        check_database(), check_elasticsearch(), check_ollama(), check_celery_status()
    )

    general_status = "ok"
    if db_status == "disconnected":
        general_status = "degraded"

    return {
        "status": general_status,
        "project": settings.PROJECT_NAME,
        "services": {
            "database": {"status": db_status},
            "elasticsearch": {"status": es_status},
            "ollama": {
                "status": ollama_status,
                "model": settings.OLLAMA_MODEL,
                "available_models": available_models
            },
            "celery": {"status": celery_status}
        }
    }


# Global/lazy instance of the plagiarism matching engine
matcher_instance = None

def get_matcher():
    global matcher_instance
    if matcher_instance is None:
        matcher_instance = DualTierMatcher()
    return matcher_instance

async def check_service_port_open(host_str: str, port_val: int) -> bool:
    import socket
    import anyio
    def check_sync():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                target = "127.0.0.1" if host_str.lower() == "localhost" else host_str
                return s.connect_ex((target, port_val)) == 0
        except Exception:
            return False
    return await anyio.to_thread.run_sync(check_sync)

async def check_postgres_online():
    from urllib.parse import urlparse
    db_host = settings.POSTGRES_HOST
    db_port = int(settings.POSTGRES_PORT or 5432)
    db_url = os.environ.get("DATABASE_URL") or settings.DATABASE_URL
    if db_url:
        try:
            parsed = urlparse(db_url)
            if parsed.hostname: db_host = parsed.hostname
            if parsed.port: db_port = parsed.port
        except Exception:
            pass
    if not await check_service_port_open(db_host, db_port):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL database service is offline. Please start the PostgreSQL Docker container (run: docker compose up -d)."
        )

async def check_elasticsearch_online():
    from urllib.parse import urlparse
    es_host = "localhost"
    es_port = 9200
    if settings.ELASTICSEARCH_URL:
        try:
            parsed = urlparse(settings.ELASTICSEARCH_URL)
            if parsed.hostname: es_host = parsed.hostname
            if parsed.port: es_port = parsed.port
        except Exception:
            pass
    if not await check_service_port_open(es_host, es_port):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Elasticsearch service is offline. Please start the Elasticsearch Docker container (run: docker compose up -d)."
        )

async def check_ollama_online():
    from urllib.parse import urlparse
    ollama_host = "127.0.0.1"
    ollama_port = 11434
    if settings.OLLAMA_URL:
        try:
            parsed = urlparse(settings.OLLAMA_URL)
            if parsed.hostname: ollama_host = parsed.hostname
            if parsed.port: ollama_port = parsed.port
        except Exception:
            pass
    if not await check_service_port_open(ollama_host, ollama_port):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ollama service is offline. Please make sure Ollama is running locally on your system."
        )

@app.post(
    f"{settings.API_V1_STR}/documents/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload and segment a document",
    description="Ingests a PDF, DOCX, or TXT file, validates constraints, extracts plain text, and segments it."
)
async def upload_document(file: UploadFile = File(...)):

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided in upload request."
        )

    # Read content
    content = await file.read()
    
    # Extract text (validations are performed internally)
    text = DocumentExtractorService.extract_text(file.filename, content)
    
    # Segment sentences with coordinates
    sentences_data = SentenceSegmenterService.segment(text)
    
    # Format response
    sentences = [
        SentenceCoordinate(
            text=s["text"],
            start_char=s["start_char"],
            end_char=s["end_char"]
        )
        for s in sentences_data
    ]
    
    return DocumentUploadResponse(
        filename=file.filename,
        text=text,
        char_count=len(text),
        sentence_count=len(sentences),
        sentences=sentences,
        analysis=None
    )

@app.post(
    f"{settings.API_V1_STR}/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Asynchronously analyze a document for plagiarism",
    description="Saves document to disk and queues background analysis task, returning a job ID immediately."
)
@app.post(
    "/api/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False
)
async def analyze_document_async(file: UploadFile = File(...)):

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided in upload request."
        )
        
    # Validate extension using settings before writing to disk
    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: .{file_ext}. Allowed types: {', '.join(settings.ALLOWED_EXTENSIONS)}"
        )
        
    # Generate UUID and setup paths
    job_id = str(uuid.uuid4())
    temp_filename = f"{job_id}_{file.filename}"
    temp_filepath = settings.UPLOAD_DIR / temp_filename
    
    # Read and save in chunks to check file size limit
    content_size = 0
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    
    try:
        with open(temp_filepath, "wb") as f:
            while chunk := await file.read(8192):
                content_size += len(chunk)
                if content_size > max_bytes:
                    raise FileSizeExceededError(f"File size exceeds limit of {settings.MAX_FILE_SIZE_MB}MB.")
                f.write(chunk)
    except FileSizeExceededError as e:
        if temp_filepath.exists():
            temp_filepath.unlink()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(e)
        )
    except Exception as e:
        if temp_filepath.exists():
            temp_filepath.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save temporary file: {str(e)}"
        )

    # Trigger celery task with custom task ID matching the job ID
    analyze_document_task.apply_async(
        args=[str(temp_filepath), file.filename],
        task_id=job_id
    )
    
    return {
        "job_id": job_id,
        "status": "pending"
    }


@app.get(
    f"{settings.API_V1_STR}/status/{{job_id}}",
    status_code=status.HTTP_200_OK,
    summary="Get background task status and results",
    description="Check the current execution status of an async document plagiarism analysis task."
)
@app.get(
    "/api/status/{job_id}",
    status_code=status.HTTP_200_OK,
    include_in_schema=False
)
async def get_job_status(job_id: str):
    res = AsyncResult(job_id, app=celery_app)
    
    if res.state == "SUCCESS":
        return {
            "job_id": job_id,
            "status": "completed",
            "result": res.result
        }
    elif res.state == "FAILURE":
        return {
            "job_id": job_id,
            "status": "failed",
            "error": str(res.result)
        }
    elif res.state in ("PENDING", "RECEIVED"):
        return {
            "job_id": job_id,
            "status": "pending"
        }
    else:  # STARTED, RETRY, etc.
        return {
            "job_id": job_id,
            "status": "processing"
        }


@app.get(
    f"{settings.API_V1_STR}/documents/report/{{job_id}}",
    summary="Download PDF report for a job",
    description="Generates and returns the official downloadable PDF plagiarism analysis report for a completed job."
)
@app.get(
    "/api/report/{job_id}",
    include_in_schema=False
)
async def get_job_report_pdf(job_id: str):
    res = AsyncResult(job_id, app=celery_app)
    
    if res.state != "SUCCESS":
        if res.state in ("PENDING", "RECEIVED", "STARTED", "RETRY"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Plagiarism analysis is still in progress. Please wait for completion before downloading the report."
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plagiarism report not found or task failed."
            )
            
    result = res.result
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plagiarism report details are empty or unavailable."
        )
        
    try:
        pdf_bytes = PDFGeneratorService.generate_report(result)
        
        filename = result.get("filename", "lemma_report.txt")
        pdf_filename = filename.rsplit(".", 1)[0] + "_report.pdf"
        
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{pdf_filename}"'
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate PDF report: {str(e)}"
        )



@app.post(
    f"{settings.API_V1_STR}/rewrite",
    response_model=RewriteResponse,
    status_code=status.HTTP_200_OK,
    summary="Rewrite a text segment to eliminate plagiarism",
    description="Uses a local LLM via Ollama to paraphrase a sentence with a professional academic tone."
)
@app.post(
    "/api/rewrite",
    response_model=RewriteResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False
)
async def rewrite_text_endpoint(payload: RewriteRequest):
    await check_ollama_online()
    rewritten = await LLMService.rewrite_text(payload.text, tone=payload.tone)
    return RewriteResponse(
        original_text=payload.text,
        rewritten_text=rewritten
    )



from pydantic import BaseModel
class AIDetectRequest(BaseModel):
    text: str

@app.post(
    f"{settings.API_V1_STR}/detect_ai",
    status_code=status.HTTP_200_OK,
    summary="Detect AI generated text"
)
async def detect_ai_endpoint(payload: AIDetectRequest):
    result = await LLMService.detect_ai(payload.text)
    return result

# Serve static frontend files
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
# Ensure the directory exists
try:
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
except Exception as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"Could not mount static frontend files directory {FRONTEND_DIR}: {e}")

