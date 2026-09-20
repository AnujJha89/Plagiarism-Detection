# Lemma: Plagiarism Analysis & Academic Text Rewriting Platform

Lemma is a high-performance, local-first plagiarism analysis and academic text rewriting platform. It features a decoupled client-server architecture, local vector-based NLP pipelines, asynchronous worker queues, and professional report automation.

---

## Key Features

* **Dual-Tier Plagiarism Matcher**: Combines classical lexical matching (TF-IDF + Cosine Similarity) with deep vector semantic indexing (Sentence-Transformers + local FAISS index) to detect both verbatim copy-pastes and complex paraphrasing.
* **Precision Coordinate Mapping**: Utilizes an optimized `spaCy` tokenization pipeline to segment documents into sentences, explicitly preserving absolute character index boundaries (`start_char`, `end_char`) for exact frontend styling.
* **Interactive 3D Visual Shell**: Features a visually stunning, premium dark-mode landing page powered by an interactive **3D Fibonacci Particle Sphere** that rotates, reacts dynamically to mouse cursor dragging/hover, and gracefully bursts and reforms.
* **Local Generative Rewriter Workspace**: Integrates with local native `Ollama` pipelines (targeting an optimized Qwen 2.5:3B custom model) to offer document-level text rewriting supporting **Academic**, **Standard**, and **Creative** tones.
* **Decoupled Async Architecture**: Implements `Celery` + `Redis` task queues to run document parsing loops in background workers.
* **HTML-to-PDF Report Automation**: Generates publication-ready PDF reports with color-coded highlighted plagiarism coordinates using the `WeasyPrint` rendering engine.

---

## Technology Stack

* **Frontend**: Basic HTML5, CSS3, and Vanilla JavaScript (Minimally designed high-contrast `#000000` dark theme with glassmorphic overlays and custom Canvas animations).
* **API Service**: FastAPI (Python) using asynchronous patterns, serving static UI assets directly.
* **NLP & ML Pipelines**: `spaCy` (optimized tokenizer), `scikit-learn` (TF-IDF), `sentence-transformers` (all-MiniLM-L6-v2 vector embeddings), `faiss` (local vector similarity search).
* **Workers & Cache**: Celery + Redis (supports in-memory eager mode fallback).
* **Storage**: SQLite (Metadata and sentence mappings).
* **Report Generation**: WeasyPrint.

---

## Repository Directory Structure

```
lemma/
├── .gitignore                   # Safe configuration to exclude local/build/binary files
├── Modelfile                    # Optimized Ollama configurations to build lemma-model
├── README.md                    # Project documentation
├── pytest.ini                   # Pytest configurations
├── requirements.txt             # Project-wide Python dependencies
├── run.bat                      # Windows automated environment setup & run script
├── Project Context/             # Project briefs and architecture documents
│   ├── database_migration_summary.md
│   ├── mission_briefing.md
│   ├── project_understanding.md
│   └── system_understanding.md
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py            # System configurations
│   │   ├── main.py              # API routes & endpoint definitions
│   │   ├── data/
│   │   │   └── mock_references.json  # Reference corpus text database for seeding
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── document.py      # Pydantic schemas for document analysis
│   │   │   └── rewrite.py       # Pydantic schemas for sentence rewriting
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── database.py      # SQLite database interaction layer
│   │   │   ├── extractor.py     # Text extraction (PDF, DOCX, TXT)
│   │   │   ├── llm.py           # Local Ollama client (with tone optimizations)
│   │   │   ├── matcher.py       # Lexical (TF-IDF) & Semantic (FAISS) matcher
│   │   │   ├── pdf_generator.py # PDF report compilation using WeasyPrint
│   │   │   └── segmenter.py     # Optimized spaCy sentence segmenter
│   │   └── tasks/
│   │       ├── __init__.py
│   │       ├── celery_app.py    # Celery queue instantiation & config
│   │       └── analysis.py      # Celery asynchronous document analysis task
│   └── tests/                   # Test suite
│       ├── __init__.py
│       ├── conftest.py          # Eager Celery environment configurations
│       ├── test_async_queue.py  # Asynchronous upload & polling tests
│       ├── test_extractor.py    # Document extractor unit tests
│       ├── test_main.py         # FastAPI endpoint integration tests
│       ├── test_matcher.py      # Lexical/semantic matcher tests
│       ├── test_pdf.py          # WeasyPrint and PDF download integration tests
│       ├── test_rewrite.py      # Mocked Ollama rewrite tests
│       └── test_segmenter.py    # Sentence segmenter unit tests
└── frontend/                    # Vanilla JS + Stark-Theme Frontend assets
    ├── app.js                   # Client side polling & rewriting controller
    ├── dashboard.html           # Plagiarism checker & Paraphraser workspaces
    ├── index.html               # Landing Page (typewriter title, particle canvas)
    ├── landing.js               # Landing page animation loop & 3D projection physics
    └── style.css                # Main styling, custom blurs, and button layouts
```

---

## Quick Start (Local Setup)

### Prerequisites

* **Python**: Version `3.10` or higher (successfully tested up to `3.13` and `3.14.3`).
* **Ollama**: Download and install Ollama from [ollama.com](https://ollama.com).

### 1. Build the Optimized Ollama Model
To avoid CUDA Out-of-Memory (OOM) errors on local GPUs/CPUs, a custom `Modelfile` is provided to set the context size to `1024` tokens and limit the prediction output window.

Open a terminal and run:
```powershell
# 1. Pull the default base model
ollama pull llama3

# 2. Build the optimized model
ollama create lemma-model -f Modelfile

# 3. Spin up the model to verify it loads
ollama run lemma-model
```

### 2. Configure Asynchronous Execution Modes
Lemma supports two modes of execution:

#### Option A: Zero-Dependency Eager Mode (Easiest)
Process background analysis synchronously inside the FastAPI process, removing the need for a separate Celery terminal or Redis instance.
1. Open `backend/app/config.py` and set `CELERY_ALWAYS_EAGER: bool = True`.
2. Launch the FastAPI server (see below).

#### Option B: Full Background Task Queue Mode (Real Async)
1. Start your local Redis server on port 6379 (`redis-server`).
2. Open `backend/app/config.py` and set `CELERY_ALWAYS_EAGER: bool = False`.
3. Start the Celery worker:
   ```powershell
   .\venv\Scripts\celery.exe -A backend.app.tasks.celery_app.celery_app worker --loglevel=info -P solo
   ```

### 3. Launch the API Server (Windows)
Double-click or run the root helper script to install dependencies, setup spaCy, and run uvicorn:
```powershell
.\run.bat
```

### 4. Visit the Application
* Access the **Web Interface** at: **[http://localhost:8000](http://localhost:8000)** (or go straight to **[http://localhost:8000/dashboard.html](http://localhost:8000/dashboard.html)**).
* Access the **Swagger API Docs** at: **[http://localhost:8000/docs](http://localhost:8000/docs)**.

---

## Core API Documentation (Implemented Endpoints)

### `GET /api/v1/health` (or `/health`)
* **Purpose**: Returns operational status.

### `POST /api/v1/analyze` (or `/api/analyze`)
* **Purpose**: Asynchronously ingests a `.txt`, `.docx`, or `.pdf` file. Saves it to disk, triggers a background Celery task, and immediately returns a job/task ID.
* **Content-Type**: `multipart/form-data`
* **Response (202 Accepted)**:
  ```json
  { "job_id": "7038a805-f8eb-4ae5-8f9d-f5b388e652ca", "status": "pending" }
  ```

### `GET /api/v1/status/{job_id}` (or `/api/status/{job_id}`)
* **Purpose**: Retrieves the execution state and results of a document analysis job.
* **Response (200 OK)**:
  * When pending: `{ "job_id": "...", "status": "pending" }`
  * When processing: `{ "job_id": "...", "status": "processing" }`
  * When completed: 
    ```json
    {
      "job_id": "...",
      "status": "completed",
      "result": {
        "filename": "essay.txt",
        "text": "Extracted text here...",
        "char_count": 120,
        "sentence_count": 5,
        "sentences": [...],
        "analysis": {
          "plagiarism_score": 0.2,
          "total_sentences": 5,
          "plagiarized_sentences_count": 1,
          "lexical_matches_count": 1,
          "semantic_matches_count": 0,
          "matches": [...]
        }
      }
    }
    ```

### `POST /api/v1/rewrite` (or `/api/rewrite`)
* **Purpose**: Paraphrases a sentence or paragraph using local LLM controls to eliminate plagiarism.
* **Payload**:
  ```json
  {
    "text": "This is a plagiarized text segment.",
    "tone": "creative" // options: "academic" (default), "standard", "creative"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "original_text": "This is a plagiarized text segment.",
    "rewritten_text": "This represents a rewritten sentence segment."
  }
  ```

### `GET /api/v1/documents/report/{job_id}` (or `/api/report/{job_id}`)
* **Purpose**: Generates and compiles a downloadable academic integrity PDF report highlighting plagiarism coordinate matches via WeasyPrint.
* **Response (200 OK)**: A binary file response containing the PDF report (`application/pdf`).


---

## Running the Test Suite

We use `pytest` for unit and integration testing. Eager mode is forced automatically in tests, removing the need for a running Redis server. Execute the test suite with:

```bash
.\venv\Scripts\python.exe -m pytest backend/tests/
```

---

## Build Roadmap

* [x] **Phase 1**: Core Ingestion, Parsing Service & spaCy Coordinate Segmenter
* [x] **Phase 2**: TF-IDF Matrix & Semantic Embeddings Dual Matching Engine (SQLite + FAISS storage)
* [x] **Phase 3**: Celery Asynchronous Job Queues, Redis Integration, and Ollama Paraphraser Workspace
* [x] **Phase 4**: Stark dark-theme Frontend with Interactive 3D Canvas and Workspace Switching
* [x] **Phase 5**: WeasyPrint PDF Generation & End-to-End E2E Verification
