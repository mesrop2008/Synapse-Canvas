# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

**Status:** Part 2 of 6 — authentication and workspaces, documents with optimistic
concurrency, and a React client with a Tiptap editor that autosaves. English and Russian.
144 backend tests.

> Detailed documentation lives separately; this file covers setup only.

## Stack

FastAPI · SQLAlchemy 2.0 (async) · Alembic · PostgreSQL · Pydantic v2 · pytest

React 18 · TypeScript · Vite · React Router · TanStack Query · Tiptap 3

## Running it

Requires Docker.

```bash
cp .env.example .env
```

```bash
docker compose up -d --build
```

| | |
|---|---|
| App | <http://localhost:5173> |
| API docs | <http://localhost:8000/docs> |
| PostgreSQL | `localhost:5433` |

Register at `/register`, then find the verification link in the API log
(`docker compose logs api`, look for `[email:console]`) and open it — no mail is sent
locally.

Every setting is commented in [`.env.example`](.env.example).

## Working on it

The web image holds compiled output, so rebuild after client changes:

```bash
docker compose up -d --build web
```

For hot reload, run the client on the host instead (stop the `web` container first):

```bash
cd web && cp .env.example .env && npm install && npm run dev
```

The API on the host, which the test suite needs:

```bash
docker compose up -d postgres
```

```bash
python -m venv .venv && .venv/Scripts/activate    # macOS/Linux: source .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt && alembic upgrade head && uvicorn api.main:app --reload
```

## Tests

```bash
pytest
```

Runs against a real PostgreSQL, each test inside a transaction that is rolled back.
`TEST_DATABASE_URL` must point at a throwaway database — the suite drops every table in it.

## Layout

```
api/            FastAPI app — core/ db/ models/ schemas/ services/ routers/
alembic/        migrations
tests/          pytest suite
web/            React client
  i18n/         language packs: one folder per language, one JSON per section
  src/          api/ hooks/ pages/ components/ types/
```

Business logic lives in `api/services/` and imports nothing from FastAPI, so Part 3's
WebSocket handlers can call the same functions the HTTP routes do.

## Roadmap

Part 3 real-time sync (WebSocket, Redis pub/sub, change log) · Part 4 AI streaming over
SSE · Part 5 file upload and pgvector retrieval · Part 6 hardening and deployment.
