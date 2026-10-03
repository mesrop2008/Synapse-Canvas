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
docker compose up -d --build
```

Open <http://localhost:5173>. No `.env` is needed: every setting has a working
default, and a development signing key is generated on first start.

Only that one port is published, so the stack starts even when the machine
already runs its own Postgres, Redis or API on the usual ports. If 5173 is
taken, pick another in `.env` (`WEB_PORT=5174`) and run the command again.

Verification codes are logged rather than mailed until SMTP is configured:

```bash
docker compose logs api | grep -A 4 "email:console"
```

To send real email, copy `.env.example` to `.env`, set `EMAIL_BACKEND=smtp`
and the `SMTP_*` settings (Gmail, Yandex and Mail.ru examples are in the file),
then `docker compose up -d`. To check them, send yourself a test message:

```bash
docker compose exec api python -m api.mailcheck you@example.com
```

The API also logs at startup whether email is really being delivered.

### Host-side development

Tests, migrations and the Vite dev server run on the host and need Postgres,
Redis and the API reachable from it. [`docker-compose.dev.yml`](docker-compose.dev.yml)
publishes them, bound to localhost:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

| | |
|---|---|
| API docs | <http://localhost:8000/docs> |
| PostgreSQL | `localhost:5433` |
| Redis | `localhost:6381` |

Each port moves with `API_PORT`, `POSTGRES_PORT` and `REDIS_PORT` in `.env`
-- change `DATABASE_URL` and `TEST_DATABASE_URL` to match. To make the overlay
the default, uncomment `COMPOSE_FILE` in `.env`.

Settings are commented in [`.env.example`](.env.example).

## Commands

| | |
|---|---|
| Rebuild the client | `docker compose up -d --build web` |
| Client with hot reload | `npm run dev --prefix web` |
| Backing services only (needs the dev overlay) | `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis` |
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
