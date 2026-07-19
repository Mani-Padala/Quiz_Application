# Quiz Application — Complete Project Documentation

**Author:** P N Manikanta Reddy
**Purpose:** Personal AI/portfolio project — a RAG-based quiz generator with real authentication, built while studying for an Auth0 TAM role, doubling as hands-on practice.

---

## 1. What This Project Does

Upload a document (PDF, DOCX, TXT, or image — including scanned/image-based PDFs via OCR) → the app splits it into sections, generates multiple-choice questions from the content using an LLM, and lets users take quizzes through a web UI with real Auth0 login. Each user's question history is tracked so they're never shown a repeated question, and they get a score, per-topic breakdown, a full answer review, and cross-session improvement suggestions.

## 2. Architecture

```
documents/ (source files)
      │
      ▼
process_documents.py   ← run manually whenever documents/ changes
      │  (loads + OCRs each file ONCE via process_all_documents())
      ▼
  ┌─────────────┬───────────────────┬─────────────────────┐
  │ faiss_index/│ chunks_cache.pkl  │ sections_cache.pkl   │
  └─────────────┴───────────────────┴─────────────────────┘
      │
      ▼
app.py (web, real Auth0 login) / main.py (CLI, fake login for local testing)
      │
      ▼
quiz.py (sessions, no-repeat logic) → generator.py (Groq) → scorer.py (scoring)
```

**Core architectural principle, established deliberately partway through the build:** `app.py` and `main.py` NEVER trigger document processing (OCR, chunking, section detection) themselves. Only `process_documents.py` does that. This exists because OCR on a large scanned PDF can take 15-30+ minutes, and that must never happen inline during a login or page load. If the caches don't exist yet, both entry points show a friendly "not processed yet, run process_documents.py" message instead of silently kicking off a long-running job inside an HTTP request.

## 3. Tech Stack

- **Backend:** Python, FastAPI (web), plain script (CLI)
- **Templates:** Jinja2
- **Auth:** Auth0 (Authlib for the OAuth client), Authorization Code grant, Regular Web Application type
- **LLM:** Groq (`llama-3.3-70b-versatile`) — **per-user API key**, entered on the quiz-start form, never stored (not in DB, not in session) — this was a deliberate late change to avoid all users sharing one server-side key's rate limits
- **Embeddings/Vector search:** HuggingFace `all-MiniLM-L6-v2` + FAISS
- **Keyword search:** BM25 (via `retriever.py` — built early on, no longer actively used now that section detection provides exact content boundaries directly; kept in the codebase, harmless)
- **OCR:** Tesseract + Poppler (system-level, not pip-installable — this is why deployment uses Docker instead of Render's default Python runtime)
- **DB:** SQLite
- **Deployment:** Render (free tier, Docker), GitHub (`Mani-Padala/Quiz_Application`)

## 4. File-by-File Reference

| File                                                            | Purpose                                                                                                                                                                                           |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ingestion.py`                                                  | Document loading, OCR (with batched page processing to avoid MemoryError on large PDFs), section detection, all three cache-building functions                                                    |
| `process_documents.py`                                          | Standalone CLI tool — the ONLY place processing/OCR ever runs                                                                                                                                     |
| `generator.py`                                                  | Groq-based MCQ generation; `FEW_SHOT_EXAMPLES` is a swappable constant — real exam papers can be loaded later via `load_few_shot_examples()` without touching other code                          |
| `quiz.py`                                                       | Session management: builds/reuses the per-topic question pool (`questions` table), tracks per-user history (`user_question_history` table) so nothing repeats, resume support via `checkpoint.py` |
| `scorer.py`                                                     | Per-topic scoring (`topic_scores` table), cross-session improvement suggestions                                                                                                                   |
| `checkpoint.py`                                                 | Pre-existing — resume-in-progress support                                                                                                                                                         |
| `database.py` / `create_table.sql`                              | SQLite schema (6 tables: `users`, `exam_sessions`, `topic_scores`, `checkpoint`, `questions`, `user_question_history`)                                                                            |
| `app.py`                                                        | Web app — FastAPI + real Auth0 login (Authorization Code flow), routes for home/quiz/results                                                                                                      |
| `main.py`                                                       | CLI app — fake/local login (just a username), same underlying quiz/scoring logic                                                                                                                  |
| `templates/`                                                    | `base.html` (shared styling — gradient hero header, colored tinted cards), `landing.html`, `home.html`, `question.html`, `results.html` (includes full answer review), `not_ready.html`           |
| `requirements.txt`, `Dockerfile`, `.dockerignore`, `.gitignore` | Deployment config                                                                                                                                                                                 |

## 5. Key Design Decisions & Why

- **No repeat questions, ever, per user:** the `questions` table is a shared pool per topic; `user_question_history` tracks what a specific user has seen. If a user has exhausted the pool, `generator.py` is called to generate more — the pool only grows, never repeats.
- **Section detection is heuristic, not perfect:** `detect_sections()` looks for headings matching "Chapter/Section/Part + number" at the start of each page. If a document doesn't use that convention (as turned out to be the case for both test documents used), everything falls into one "Full Document" bucket — this is a known, accepted limitation, not a bug. A better future fix would read a PDF's actual embedded bookmarks/table of contents instead of guessing from text.
- **Context chunks are capped at 15 per generation call** (`MAX_CONTEXT_CHUNKS_PER_GENERATION` in `quiz.py`) — discovered as a real bug when the "Full Document" bucket (containing thousands of chunks) caused a 500 error by exceeding Groq's context limit in one prompt.
- **Each document is loaded/OCR'd exactly once per processing run**, not three times — an inefficiency caught mid-build where `ingest_documents()`, `get_all_chunks()`, and `get_document_sections()` were each independently re-loading every file. Fixed via `_load_all_raw_documents()` (cached in `raw_documents_cache.pkl`), which all three now derive from.
- **Per-user Groq API keys, entered fresh each quiz-start, never persisted** — added specifically so multiple friends using the app don't share/exhaust one rate-limited key.
- **No long-term user tracking guarantee on Render's free tier** — the filesystem is ephemeral, so `quiz_app.db` (scores/history) may reset on container restart. Accepted tradeoff for a free, low-stakes friend-testing deployment. Documents and their processed caches survive restarts fine, since they're baked into the Docker image at build time, not written at runtime.

## 6. Real Bugs Hit & Fixed (useful troubleshooting stories)

- **`retriever.py` was an empty stub** (just imports + function signature) — caused a silent script exit with no traceback in some terminal configurations.
- **`MemoryError` OCR'ing a 391-page PDF** — `pdf2image.convert_from_path()` was rendering all pages into memory at once. Fixed by processing in batches of 10 pages, discarding each batch before the next.
- **PowerShell's `>>` redirection corrupted a SQL file** — injected a UTF-16 BOM and mojibake characters, causing a cryptic `sqlite3.OperationalError: near "-"`. Fixed by rewriting the file with explicit UTF-8 encoding.
- **`TypeError: unhashable type: 'dict'`** — a Starlette/Jinja2 version compatibility issue from using the older `TemplateResponse(name, {"request": request})` calling style. Fixed by switching to `TemplateResponse(request, name, context)`.
- **GitHub rejected a 124MB PDF** (100MB limit) — fixed by untracking it from git (`git rm --cached`) and adding `documents/` to `.gitignore`, since the app never needs the raw file at runtime anyway, only the processed caches.
- **A trailing space in a copy-pasted Groq API key** corrupted the `Authorization: Bearer <token>` HTTP header at the protocol level (`httpcore.LocalProtocolError: Illegal header value`) — directly mirrored an earlier Postman lesson about whitespace corrupting Bearer headers. Fixed with `.strip()` at both the point of entry (`app.py`) and defensively again inside `generator.py`.

## 7. Current State (as of last session)

Fully working end-to-end: real Auth0 login (sign up + log in), document upload → OCR → quiz generation → scoring → full answer review, deployed live on Render, shared with friends for real-world testing feedback on question quality.

## 8. Possible Future Improvements (not yet done, not urgent)

- Read PDF bookmarks/table of contents for real section detection instead of text-pattern heuristics
- A cheap pre-check (OCR just the top strip of each page at low resolution) to skip full-page OCR on known-irrelevant sections (e.g. large annexure blocks) without hardcoding page numbers
- Permanent (not just immediate-post-quiz) answer review, which would need a new table storing every individual answer rather than relying on the temporary checkpoint
- Document upload feature within the app itself (currently: admin manually drops files into `documents/` and re-runs `process_documents.py`)
- Migrate off SQLite to a persistent hosted DB if long-term score history across Render restarts becomes important

---

_Next focus area (per user): moving from building the app to using it, and real Auth0 tenant experimentation, as hands-on practice for Okta/Auth0 TAM interview scenarios — testing real callback mismatches, session expiry, log review via Auth0's Monitoring dashboard, etc._
