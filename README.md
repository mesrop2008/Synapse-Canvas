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

### Embedding providers

| `EMBEDDING_PROVIDER` | |
|---|---|
| `gemini` (default) | `gemini-embedding-001` on the same `GEMINI_API_KEY` as the assistant. |
| `local` | `paraphrase-multilingual-MiniLM-L12-v2` on the CPU: English and Russian, no key at all. |
| `fake` | Word-hash vectors for the test suite; refused outside `local` and `test`. |

**Gemini.** The model returns 3072 dimensions by default; pgvector 0.8.7 can
index at most 2,000 with HNSW, so the API is asked for `EMBEDDING_DIMENSION`
(1536). Each text may be at most 2,048 tokens, and a longer one is cut without
an error, so every passage is checked against the limit before anything is
sent, with a token estimate that over-counts. The free tier allows about 100
requests a minute and 1,000 a day: passages go `EMBEDDING_BATCH_SIZE` to a
request, a 429 is retried with exponential backoff (not when Gemini says the
daily quota is gone), and every request -- retries and search queries included
-- counts against `EMBEDDING_DAILY_REQUEST_LIMIT` per workspace, so running out
shows on the source as a quota failure instead of a stuck upload.

**Running with no key.** Put these in `.env` and rebuild:

```bash
INSTALL_LOCAL_EMBEDDINGS=true   # adds CPU torch and sentence-transformers to the images
EMBEDDING_PROVIDER=local
EMBEDDING_DIMENSION=384
```

The model (about 470 MB) is downloaded on first use and kept on a volume. Its
inputs stop at 512 tokens, so passages are smaller than with Gemini. On the
host, `pip install -r requirements-local.txt`.

### Switching models means re-embedding

Vectors from different models, or of different sizes, cannot be compared.
Every passage records its model and dimension; a search refuses a workspace
that holds more than one model, or another model than the one configured; and
the API and worker refuse to start when `EMBEDDING_DIMENSION` no longer matches
the `vector(N)` column the migration created. The local model's 384 dimensions
differ from Gemini's 1536, so switching between them, or changing the
dimension, costs a migration of the column and a full re-embed. Stop the API
and the worker, change the settings, run

```sql
BEGIN;
DELETE FROM source_chunks;
ALTER TABLE source_chunks ALTER COLUMN embedding TYPE vector(384);  -- the new size
UPDATE sources SET status = 'pending', error_code = NULL, chunk_count = 0, processed_at = NULL;
COMMIT;
```

and start them again: the worker's sweep queues every source within a minute,
and each costs its embedding requests again. On a fresh database the first
`alembic upgrade head` creates the column at `EMBEDDING_DIMENSION` already.

### Retrieval

`POST /workspaces/{id}/search` returns the passages closest to a query with
their source, page, offsets and similarity -- the same retrieval the AI uses,
for judging it before it reaches a model. The query is compared by cosine
distance with this workspace's passages only: the filter is part of the SQL,
with pgvector's iterative index scan so a small workspace in a large table
still gets its own nearest rows. Then a similarity floor
(`RETRIEVAL_MIN_SIMILARITY`, by default the model's own: 0.6 for Gemini, 0.35
for the local model), so an unrelated question finds nothing; one of any
near-identical passages of a source; and `RETRIEVAL_TOKEN_BUDGET`.

### Citations

Passages go into the prompt as `<source marker="[n]">`, and the model is told
to end a sentence that relies on one with its marker and never to write file
names or links. When the answer completes, the server reads the markers and
resolves each against the passages that request actually sent; a number with
no passage behind it is dropped. The query keeps the ids of the passages it
was sent and its resolved citations, so a past answer still names its sources,
and a citation whose source has since been deleted reads as unavailable --
the panel lists your earlier answers on the document this way.
In **sources** mode with nothing relevant found, the model is never asked: the
answer says the sources do not cover the question.

Uploaded files are untrusted in a way the document is not: a PDF can carry text
written to steer the model. Passages are fenced so they cannot close their tag,
and the system instruction says they are evidence, never instructions. That
narrows prompt injection; it does not prevent it, which is one reason answers
stay proposals the user inserts, and citations come from the server rather
than from the model.

With `LLM_PROVIDER=fake` the scripted reply cites the first passage it was
sent, so a keyless setup shows citations end to end.

### Not done yet

- Scanned PDFs are refused rather than run through OCR.
- An inserted answer keeps its `[n]` markers as plain text, without a list of
  the sources behind them.
- Uploads are capped by `Content-Length` and nginx; a chunked upload is still
  read to the end before the size check refuses it.


## Roadmap

| Part | |
|---|---|
| 6 | Hardening and deployment |
