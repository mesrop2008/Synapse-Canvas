# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

Multiple people can edit the same document simultaneously, see each other's cursors, and
watch AI responses stream in live. The technically interesting part is keeping several
browser clients consistent with each other and with the server while edits, presence
updates, and token streams all flow at once.

**Status:** Part 1 of 6 complete — authentication, workspace management, and a security
hardening pass. 109 tests, 84% line coverage.

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI, Python 3.11+ |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Database | PostgreSQL |
| Validation | Pydantic v2 |
| Auth | JWT (access + refresh), bcrypt via passlib |
| Tests | pytest, httpx AsyncClient |

Planned for later parts: React + TypeScript, Tiptap, Redis, pgvector, Celery or Arq.

## Running locally

```bash
cp .env.example .env
```

Set `JWT_SECRET_KEY` in `.env` (minimum 32 characters):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Start PostgreSQL. It is published on host port **5433**, so it does not collide with a
natively installed server on 5432:

```bash
docker compose up -d postgres
```

```bash
python -m venv .venv && .venv/Scripts/activate    # macOS/Linux: source .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt
```

```bash
alembic upgrade head
```

```bash
uvicorn app.main:app --reload
```

To run the whole stack, API included, in Docker instead: `docker compose up -d --build`.

### Trying the API

There is no user interface yet. Explore the API at `http://localhost:8000/docs`, which serves
FastAPI's generated Swagger UI.

A new account must verify its email before it can log in. Locally no mail is sent — the
verification link is written to the API log. So the order is:

1. `POST /auth/register`
2. Copy the link from the API log and redeem its token at `POST /auth/verify-email`
   (skip this and login returns 403)
3. `POST /auth/login`, then click **Authorize** and paste the access token — no `Bearer`
   prefix, the scheme adds it
4. Call the workspace endpoints

### Tests

```bash
pytest
```

```bash
pytest --cov=app
```

Tests run against a real PostgreSQL, each inside a transaction that is rolled back
afterwards, so they are order-independent and leave no rows behind.

If `docker compose` fails with `tls: server did not echo the legacy session ID`, something on
the network path — a proxy, VPN, or antivirus HTTPS scanning — is terminating TLS to Docker
Hub.

## Roadmap

### Part 2 — Documents and the React shell

`Document` model with content stored as structured JSON rather than HTML, plus REST CRUD. A
React + TypeScript client with routing, the login flow wired to Part 1, and a Tiptap editor
that autosaves over plain HTTP. By the end, one user can create a document, type, refresh, and
find their text intact.

### Part 3 — Real-time sync

The hard part. A WebSocket endpoint per document, a connection manager, and version-tracked
edits in an append-only `DocumentChange` log that becomes the source of truth for sync and,
later, history.

Redis pub/sub broadcasts edits across backend processes — without it, two users on different
worker processes cannot see each other, since each process holds its own in-memory set of
connections.

Conflict handling starts as reject-and-rebase: the server holds the authoritative version
number, and a client whose edit carries a stale version refetches and reapplies. Operational
transform is the follow-up, once the simpler approach works and its failure modes are
understood. Presence and remote cursors ship here too.

### Part 4 — AI integration

Server-sent events for streaming LLM responses, kept separate from the edit WebSocket. Prompt
assembly from document context, `AIQuery` persistence, and a UI that renders tokens as they
arrive.

### Part 5 — Retrieval

File upload with a background worker that chunks and embeds source documents into pgvector.
Semantic search over those chunks, and AI answers that cite the sources they drew from.

### Part 6 — Hardening

Per-workspace token and cost tracking, workspace invitations, real error and reconnection
states in the UI, and deployment. (Rate limiting and the auth hardening landed early, in
Part 1.)
