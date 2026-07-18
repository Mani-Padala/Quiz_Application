# Quiz Application

Generate multiple-choice quizzes automatically from your own documents (PDF, DOCX, TXT, scanned/image-based PDFs via OCR), take them through a web UI with real Auth0 login, and track your score history and improvement suggestions over time.

## Features

- **Document ingestion**: PDF, DOCX, TXT, and image files. Scanned/image-based PDFs are automatically OCR'd (Tesseract + Poppler).
- **Question generation**: Groq-powered MCQ generation from retrieved document context, with easy/medium/hard difficulty.
- **No repeated questions**: each user is tracked per-question, across every attempt — the question pool grows automatically instead of ever repeating.
- **Resume support**: an interrupted quiz picks up exactly where you left off.
- **Scoring**: overall score, per-topic breakdown, and cross-session improvement suggestions.
- **Real login**: Auth0 Authorization Code flow (Universal Login — supports both log in and sign up).
- **Two entry points**: a CLI version (`main.py`) and a web version (`app.py`), sharing all the same underlying logic (`quiz.py`, `scorer.py`, `generator.py`).

## Architecture

```
documents/ (your source files)
      │
      ▼
process_documents.py   ← run manually whenever documents change
      │  (loads + OCRs each file ONCE, builds all 3 caches below)
      ▼
  ┌─────────────┬───────────────────┬─────────────────────┐
  │ faiss_index/│ chunks_cache.pkl  │ sections_cache.pkl   │
  └─────────────┴───────────────────┴─────────────────────┘
      │
      ▼
app.py (web, real Auth0 login) / main.py (CLI, fake login)
      │
      ▼
quiz.py (sessions, no-repeat logic) → generator.py (Groq) → scorer.py (scoring)
```

**Important**: `app.py` and `main.py` never trigger document processing themselves — they only read the caches `process_documents.py` produces. This keeps login and page loads instant, even for large scanned documents that take a long time to OCR.

## Local setup

### 1. Install dependencies

```
pip install -r requirements.txt
```

### 2. Install system-level OCR tools (only needed if you have scanned PDFs)

- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (Windows) or `apt install tesseract-ocr` (Linux)
- [Poppler](https://github.com/oschwartz10612/poppler-windows/releases) (Windows) or `apt install poppler-utils` (Linux)

Update `TESSERACT_CMD` and `POPPLER_PATH` in `ingestion.py` (or set them as environment variables) to match your machine.

### 3. Create a `.env` file (never commit this)

```
AUTH0_DOMAIN=your-tenant.us.auth0.com
AUTH0_CLIENT_ID=your_client_id
AUTH0_CLIENT_SECRET=your_client_secret
SESSION_SECRET_KEY=any_random_long_string
GROQ_API_KEY=your_groq_key
```

### 4. Set up your Auth0 application

- Application type: **Regular Web Application**
- Allowed Callback URLs: `http://localhost:8000/callback`
- Allowed Logout URLs: `http://localhost:8000`

### 5. Add your documents

Put PDF/DOCX/TXT/image files into `documents/`.

### 6. Process the documents (once, or whenever documents/ changes)

```
python process_documents.py
```

### 7. Run the app

Web version:

```
uvicorn app:app --reload
```

Then visit `http://localhost:8000`.

CLI version:

```
python main.py
```

## Deployment (Render, free tier)

Render's default Python deploy can't install Tesseract/Poppler (system-level tools), so this project deploys via **Docker** instead. See `Dockerfile`.

1. Run `python process_documents.py` locally first — the Docker image bakes in whatever's already in `documents/`, `chunks_cache.pkl`, `sections_cache.pkl`, and `faiss_index/` at build time. Render's free tier has no persistent disk to build these on its own.
2. Push everything (including the cache files and `documents/`) to GitHub — see steps below.
3. On Render: **New → Web Service** → connect your repo → Environment auto-detected as **Docker** → Instance Type **Free**.
4. Add the same 5 environment variables from your `.env` file under Render's **Environment Variables** settings.
5. Deploy. Once live, add the Render URL to your Auth0 app's Allowed Callback/Logout URLs (keep the `localhost` ones too).

**Known limitations of the free tier**: the service spins down after inactivity (first request after idle can take 30-60s to wake up), and the filesystem is ephemeral — quiz history (`quiz_app.db`) may reset on restart. Documents and their processed caches are safe, since they're baked into the Docker image itself, not written at runtime.

## Project files

| File                               | Purpose                                                    |
| ---------------------------------- | ---------------------------------------------------------- |
| `ingestion.py`                     | Document loading, OCR, chunking, section detection         |
| `process_documents.py`             | Standalone tool — run manually to (re)process `documents/` |
| `generator.py`                     | Groq-based question generation                             |
| `quiz.py`                          | Quiz session management, no-repeat question pool logic     |
| `scorer.py`                        | Per-topic scoring and improvement suggestions              |
| `database.py` / `create_table.sql` | SQLite schema and connection                               |
| `checkpoint.py`                    | Resume-in-progress support                                 |
| `app.py`                           | Web app (FastAPI + Auth0 login)                            |
| `main.py`                          | CLI app (fake login, same underlying logic)                |
| `templates/`                       | Jinja2 HTML templates for the web app                      |
