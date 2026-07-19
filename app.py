"""
app.py — FastAPI web version of the Quiz Application, with real Auth0 login.

This replaces main.py's fake get_or_create_user() with a real Authorization
Code flow against your Auth0 tenant — everything else (quiz.py, scorer.py,
generator.py, database.py) is reused completely unchanged. That's the whole
point of keeping user_id as a plain string throughout the codebase: it never
mattered whether it came from a CLI input() or a real identity provider.

Setup (one-time):
    pip install fastapi uvicorn jinja2 authlib itsdangerous python-multipart python-dotenv

Create a .env file in your project root (never commit this):
    AUTH0_DOMAIN=dev-052csl7uk4ed2zny.us.auth0.com
    AUTH0_CLIENT_ID=your_client_id
    AUTH0_CLIENT_SECRET=your_client_secret
    SESSION_SECRET_KEY=some_random_long_string_here

In your Auth0 dashboard (Quizz App -> Settings -> Application URIs), add to
Allowed Callback URLs:
    http://localhost:8000/callback
And to Allowed Logout URLs:
    http://localhost:8000

Run with:
    uvicorn app:app --reload
"""

import os
import uuid
from datetime import datetime
from urllib.parse import quote_plus, urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth

from database import create_tables, get_db_connection
from ingestion import get_document_sections, SECTIONS_CACHE_PATH
from checkpoint import load_checkpoint
import quiz
import scorer


load_dotenv()

AUTH0_DOMAIN = os.environ["AUTH0_DOMAIN"]
AUTH0_CLIENT_ID = os.environ["AUTH0_CLIENT_ID"]
AUTH0_CLIENT_SECRET = os.environ["AUTH0_CLIENT_SECRET"]
SESSION_SECRET_KEY = os.environ["SESSION_SECRET_KEY"]

DOCUMENTS_FOLDER = "documents"

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)
templates = Jinja2Templates(directory="templates")

create_tables()

# ---------------------------------------------------------------------------
# Auth0 OAuth client setup
# ---------------------------------------------------------------------------

oauth = OAuth()
oauth.register(
    name="auth0",
    client_id=AUTH0_CLIENT_ID,
    client_secret=AUTH0_CLIENT_SECRET,
    client_kwargs={"scope": "openid profile email"},
    server_metadata_url=f"https://{AUTH0_DOMAIN}/.well-known/openid-configuration",
)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("callback")
    return await oauth.auth0.authorize_redirect(request, redirect_uri)


@app.get("/callback")
async def callback(request: Request):
    token = await oauth.auth0.authorize_access_token(request)
    userinfo = token["userinfo"]

    # This is the ONLY place real Auth0 identity gets bridged into your
    # existing schema — same idea as main.py's get_or_create_user(), except
    # user_id now comes from Auth0's real 'sub' claim instead of a typed
    # username, and there's no password stored anywhere in your own database.
    user_id = get_or_create_user_from_auth0(userinfo)

    request.session["user_id"] = user_id
    request.session["name"] = userinfo.get("name", userinfo.get("email", "there"))
    return RedirectResponse(url="/")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return_url = request.url_for("home")
    logout_url = (
        f"https://{AUTH0_DOMAIN}/v2/logout?"
        + urlencode({"returnTo": str(return_url), "client_id": AUTH0_CLIENT_ID}, quote_via=quote_plus)
    )
    return RedirectResponse(url=logout_url)


def get_or_create_user_from_auth0(userinfo):
    """
    Maps an authenticated Auth0 user to your existing `users` table.
    Uses Auth0's 'sub' claim (e.g. "auth0|6a5b233c...") as user_id directly
    — it's already a stable, unique identifier, no need to generate our own.
    """
    auth0_sub = userinfo["sub"]
    email = userinfo.get("email", "")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (auth0_sub,))
    row = cursor.fetchone()

    if row is None:
        cursor.execute(
            "INSERT INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
            (auth0_sub, email, datetime.now().isoformat())
        )
        conn.commit()

    conn.close()
    return auth0_sub


def require_login(request: Request):
    """Dependency for protected routes — redirects to login if no session."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user_id


# ---------------------------------------------------------------------------
# App routes
# ---------------------------------------------------------------------------

@app.get("/")
async def home(request: Request):
    user_id = request.session.get("user_id")
    name = request.session.get("name")

    if not user_id:
        return templates.TemplateResponse(request, "landing.html", {})

    if not os.path.exists(SECTIONS_CACHE_PATH):
        return templates.TemplateResponse(request, "not_ready.html", {"name": name})

    existing_session = get_incomplete_session(user_id)

    return templates.TemplateResponse(request, "home.html", {
        "name": name,
        "has_incomplete_session": existing_session is not None,
        "incomplete_session_id": existing_session,
    })


def get_incomplete_session(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT session_id FROM exam_sessions
        WHERE user_id = ? AND completed = 0
        ORDER BY created_at DESC LIMIT 1
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


@app.post("/quiz/start")
async def start_quiz_route(
    request: Request,
    groq_api_key: str = Form(...),
    difficulty: str = Form("medium"),
    questions_per_topic: int = Form(10),
    user_id: str = Depends(require_login),
):
    if not os.path.exists(SECTIONS_CACHE_PATH):
        return RedirectResponse(url="/", status_code=303)

    all_sections = get_document_sections(DOCUMENTS_FOLDER)

    # "questions_per_topic" here is really "total questions requested" from
    # the user's perspective — divided evenly across however many sections
    # were actually detected (currently just one, "Full Document", but this
    # stays correct if section detection ever improves to find real chapters).
    num_sections = max(1, len(all_sections))
    per_section_count = max(1, questions_per_topic // num_sections)

    # groq_api_key flows straight through to generator.py for this one call
    # chain — never written to the session, the database, or disk anywhere.
    # Each user consumes their own free-tier quota instead of sharing one key.
    session_id = quiz.start_quiz(user_id, all_sections, difficulty, per_section_count, api_key=groq_api_key)
    return RedirectResponse(url=f"/quiz/{session_id}", status_code=303)


@app.get("/quiz/{session_id}")
async def quiz_question(request: Request, session_id: str, user_id: str = Depends(require_login)):
    question = quiz.get_current_question(session_id, user_id)

    if question is None:
        return RedirectResponse(url=f"/quiz/{session_id}/results", status_code=303)

    return templates.TemplateResponse(request, "question.html", {
        "session_id": session_id,
        "question": question,
    })


@app.post("/quiz/{session_id}/answer")
async def submit_answer_route(
    request: Request,
    session_id: str,
    selected: str = Form(...),
    user_id: str = Depends(require_login),
):
    quiz.submit_answer(session_id, user_id, selected)
    return RedirectResponse(url=f"/quiz/{session_id}", status_code=303)


@app.get("/quiz/{session_id}/results")
async def quiz_results(request: Request, session_id: str, user_id: str = Depends(require_login)):
    checkpoint = load_checkpoint(session_id, user_id)

    breakdown = {}
    summary = None
    if checkpoint is not None:
        breakdown = scorer.save_topic_scores(session_id, user_id, checkpoint["answered_questions"])
        summary = quiz.finish_quiz(session_id, user_id)

    overall = scorer.get_overall_summary(user_id)

    return templates.TemplateResponse(request, "results.html", {
        "summary": summary,
        "breakdown": breakdown,
        "overall": overall,
    })