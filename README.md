# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

**Part 4 of 6** — auth, workspaces, documents edited live over a WebSocket, and an
AI assistant whose responses stream over server-sent events and are inserted as
ordinary versioned edits. English and Russian. 280 backend tests.

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
| LLM | Gemini via `google-genai`, or a scripted fake |
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


## AI assistant

Beside the editor, editors and owners get a panel with four modes: **continue**
from the cursor, **rewrite** the selection, **summarize** the document, or
**ask** about it. Tokens render as they arrive; the finished response is a
preview, and nothing enters the document until **Insert**.

### Running without a key

Nothing to do: `LLM_PROVIDER` defaults to `fake`, which streams a scripted reply
word by word (`FAKE_LLM_DELAY_SECONDS` apart) with no key and no network. The
whole feature — streaming, cancel, insert, budgets — works against it, and every
test uses it. It is refused outside `ENVIRONMENT=local` or `test`, so a
deployment cannot ship it by accident.

### Using Gemini

```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.5-flash
```

Without a key the app still starts; it logs a warning and each query fails with
`ai.invalid_key`. The key is a `SecretStr` and is never logged.

### Providers

[`api/llm/base.py`](api/llm/base.py) defines `LLMProvider`: one `stream()`
method yielding text chunks and then a single `Usage` (prompt tokens,
completion tokens, model). [`GeminiProvider`](api/llm/gemini.py) and
[`FakeProvider`](api/llm/fake.py) implement it, and `LLM_PROVIDER` picks one at
startup. SDK failures are mapped onto our own errors in
[`api/llm/errors.py`](api/llm/errors.py) — rate limited, context too long,
content filtered, upstream unavailable, invalid key — each carrying the error
code the client translates, so no SDK exception reaches a router. A second
provider is one more class and one more branch in `build_provider`.

### Budgets

Per workspace, enforced when a query is created (429 with its own code and
message) and counted when it ends:

| Setting | Default | |
|---|---|---|
| `AI_DAILY_TOKEN_LIMIT` | 200000 | Prompt + completion tokens per UTC day; 0 turns it off |
| `AI_MAX_CONCURRENT_QUERIES` | 2 | Generations streaming at once; 0 turns it off |
| `AI_CONTEXT_TOKEN_LIMIT` | 6000 | Document text sent with one query |
| `AI_MAX_OUTPUT_TOKENS` | 1024 | Per response |
| `AI_RECONNECT_GRACE_SECONDS` | 10 | How long a generation survives with no reader |

`GET /workspaces/{id}/usage` returns the day's use, and the panel shows what is
left. A stopped generation still counts, at an estimate: the provider reports
usage only at the end, but the prompt and partial output were billed.

## Roadmap

| Part | |
|---|---|
| 5 | File upload and pgvector retrieval |
| 6 | Hardening and deployment |
