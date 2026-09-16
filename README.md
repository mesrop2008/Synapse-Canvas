# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

**Part 2 of 6** — auth, workspaces, documents with optimistic concurrency, and a React
client with an autosaving Tiptap editor. English and Russian. 144 backend tests.

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI, Python 3.11+ |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Database | PostgreSQL |
| Validation | Pydantic v2 |
| Client | React 18, TypeScript, Vite |
| Routing | React Router |
| Server state | TanStack Query |
| Editor | Tiptap 3 |
| Tests | pytest, httpx |

## Quick start

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

Settings are commented in [`.env.example`](.env.example).

## Commands

| | |
|---|---|
| Rebuild the client | `docker compose up -d --build web` |
| Client with hot reload | `npm run dev --prefix web` |
| Database only | `docker compose up -d postgres` |
| Host dependencies | `pip install -r requirements-dev.txt` |
| Migrations | `alembic upgrade head` |
| API on the host | `uvicorn api.main:app --reload` |
| Tests | `pytest` |

## Layout

```
api/            FastAPI app — core/ db/ models/ schemas/ services/ routers/
alembic/        migrations
tests/          pytest suite
web/
  i18n/         language packs — one folder per language, one JSON per section
  src/          api/ hooks/ pages/ components/ types/
```

## Roadmap

| Part | |
|---|---|
| 3 | Real-time sync — WebSocket, Redis pub/sub, change log |
| 4 | AI responses streamed over SSE |
| 5 | File upload and pgvector retrieval |
| 6 | Hardening and deployment |
