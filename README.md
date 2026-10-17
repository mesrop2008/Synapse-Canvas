# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

**Part 5 of 6** — auth, workspaces, documents edited live over a WebSocket, an AI
assistant whose responses stream over server-sent events and are inserted as
ordinary versioned edits, and uploaded sources it answers from with citations.
Password recovery by emailed code. English and Russian. 450 backend tests.

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI, Python 3.11+ |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Database | PostgreSQL 15 with pgvector 0.8.7 |
| Presence, pub/sub, job queue | Redis |
| Background jobs | Arq |
| Validation | Pydantic v2 |
| Client | React 18, TypeScript, Vite |
| Routing | React Router |
| Server state | TanStack Query |
| Editor | Tiptap 3 |
| LLM | Gemini via `google-genai`, or a scripted fake |
| Embeddings | `gemini-embedding-001`, or a local sentence-transformers model |
| File parsing | pypdf, python-docx |
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

Uploaded sources are embedded with Gemini by default, which needs
`GEMINI_API_KEY`; to run with no key at all, see
[Running with no key](#embedding-providers) below.

Coming from Part 4, the same command moves Postgres to an image with pgvector.
It is the same major version (15), so the data volume carries over, and the
migration enables the extension.

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
| Worker on the host | `arq api.worker.WorkerSettings` |
| Worker logs | `docker compose logs -f worker` |
| Tests | `pytest` |


## AI assistant

Beside the editor, editors and owners get a panel with five modes: **continue**
from the cursor, **rewrite** the selection, **summarize** the document, **ask**
about it and the workspace's sources, or answer from the **sources** alone.
Tokens render as they arrive; the finished response is a preview, and nothing
enters the document until **Insert**.

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



## Sources and citations

The workspace page has a **Sources** panel: editors drop in PDF, Word (.docx),
plain-text and Markdown files, watch each one move from *queued* through
*reading* to *ready*, and see storage used against the workspace's quota. In the
AI panel, **ask** now searches those sources as well as the document, and a
fifth mode, **sources**, answers from the sources alone. An answer's `[1]`
markers become buttons once it is done; each opens the passage it cites, with
its file and page, and the files an answer used are listed beneath it.

### How a file becomes searchable

1. `POST /workspaces/{id}/sources` checks the file and stores it under a
   generated name, then answers at once with the source `pending`. The type is
   decided from the bytes, not the name or the browser's word: a PDF by its
   header, a DOCX by the parts inside its ZIP, text by decoding it (UTF-8,
   UTF-16, or a detected legacy encoding such as Windows-1251). Other ZIPs,
   compressed files and archives are refused, and so is a DOCX whose contents
   would expand far past its size. The same file uploaded to the same workspace
   again returns the first copy rather than being embedded twice.
2. The **worker** (`arq api.worker.WorkerSettings`, the `worker` service in
   compose) extracts the text in a child process it kills if parsing overruns,
   cuts it into passages of about `CHUNK_TARGET_TOKENS` at paragraph, sentence,
   line and only then word boundaries, embeds them in batches and stores them
   with their page and character offsets.
3. The source turns `ready`, or `failed` with a reason the panel words for
   itself. A used-up quota is worth retrying later and offers a retry; a
   damaged file, a password-protected one, or a scan with no text layer (OCR
   is not supported) is not, and says so.

The job can run twice, or crash half-way, without duplicating a passage or
paying for one again: under a per-source lock it keeps the passages it would
produce identically, deletes the rest before embedding what is missing, and
commits after each batch. Redis keeps nothing across a restart, so the worker
also queues again, once a minute, any source left pending.


