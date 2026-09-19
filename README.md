# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

**Part 3 of 6** — auth, workspaces, and documents edited live over a WebSocket, with an
append-only change log, Redis presence and remote cursors. English and Russian.
161 backend tests.

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI, Python 3.11+ |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Database | PostgreSQL |
| Presence, pub/sub | Redis |
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
| Redis | `localhost:6381` |

Settings are commented in [`.env.example`](.env.example).

## Commands

| | |
|---|---|
| Rebuild the client | `docker compose up -d --build web` |
| Client with hot reload | `npm run dev --prefix web` |
| Backing services only | `docker compose up -d postgres redis` |
| Host dependencies | `pip install -r requirements-dev.txt` |
| Migrations | `alembic upgrade head` |
| API on the host | `uvicorn api.main:app --reload` |
| Tests | `pytest` |

## Layout

```
api/            FastAPI app — core/ db/ models/ schemas/ services/ routers/
  realtime/     connection registry, Redis relay, per-socket session loop
alembic/        migrations
tests/          pytest suite
web/
  i18n/         language packs — one folder per language, one JSON per section
  src/          api/ editor/ hooks/ pages/ components/ types/
```

## Roadmap

| Part | |
|---|---|
| 4 | AI responses streamed over SSE |
| 5 | File upload and pgvector retrieval |
| 6 | Hardening and deployment |
