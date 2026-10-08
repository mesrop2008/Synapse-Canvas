# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

**Part 4 of 6** — auth, workspaces, documents edited live over a WebSocket, and an
AI assistant whose responses stream over server-sent events and are inserted as
ordinary versioned edits. English and Russian. 299 backend tests.

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

### How it works, and why

- **Create, then stream.** `POST /documents/{id}/ai/queries` checks limits,
  builds the prompt, stores the row and starts the generation in a task of its
  own; `GET .../{query_id}/stream` follows it. A dropped connection reconnects
  with `Last-Event-ID` and resumes the same generation instead of paying for a
  second one.
- **One row write per query.** Tokens go into a Redis stream under the query id,
  with a TTL, so any worker can serve a reader. Postgres sees the insert and one
  final update.
- **`fetch`, not `EventSource`.** `EventSource` cannot send `Authorization`.
  Reading the stream with `fetch` keeps the one auth path, refresh included, and
  needs no ticket round trip per reconnect; resuming is ours to do.
- **Cancellation reaches the provider.** Calling `aclose()` on the Gemini SDK's
  stream does not close its HTTP response; that is left to the garbage
  collector. Cancelling the task awaiting the next chunk does close it. So the
  provider is pumped by a task that awaits nothing else, and the runner cancels
  it on a cancel request, when no reader heartbeat has been seen for
  `AI_RECONNECT_GRACE_SECONDS`, or on overrun. Tests check this against the real
  SDK on a mock transport.
- **Prompts** ([`api/services/prompts.py`](api/services/prompts.py)): rewrite
  sends the selection and its surroundings, continue the text before the
  cursor, summarize and ask the whole document. Over the ceiling the text is
  cut from the middle, keeping the opening and never the selection. The
  wording is in `api/i18n/{en,rus}/ai.json`, in the user's language. Document
  text is fenced in tags the system instruction calls data, which mitigates
  prompt injection but does not solve it. `sources_section()` is empty until
  Part 5 passes retrieved sources in.
- **Insert is an ordinary edit.** `apply` builds the change on the server's copy
  at the version the client names and hands it to `documents.apply_change` —
  the same row lock, version check, change log and Redis broadcast as typing —
  attributed to the user. It travels as a ProseMirror `ReplaceStep`, so peers
  replay it; the server-side edit was checked against `prosemirror-transform`
  on about 2,000 generated cases. A retried insert carries the same version, so
  it cannot land twice. The client maps the query's range through edits made
  since, and waits for its own keystrokes to be acknowledged first.
- **Queries are private** to their author: history and streams are scoped to
  the caller.

### Before production

- Budget with the provider's own token count, not the ~3 characters a token
  estimate, and reserve `AI_MAX_OUTPUT_TOKENS` at creation: today a day can
  overshoot by one response.
- Run generations on a worker pool or queue apart from the API processes, so a
  deploy does not end them (now they fail with `ai.interrupted`).
- Retry 429 and 503 from the provider with backoff, behind a circuit breaker;
  SDK retries are off so a user is not left waiting silently.
- Parse Markdown in replies into headings and lists. Today they are plain
  paragraphs, and a rewrite that crosses blocks flattens them.
- Add per-user limits and a budget in money rather than tokens, since prices
  differ by model; add metrics for time to first token and spend.
- Merge AI inserts with concurrent typing (a CRDT such as Yjs) instead of
  refusing them with 409 to be retried.

## Roadmap

| Part | |
|---|---|
| 5 | File upload and pgvector retrieval |
| 6 | Hardening and deployment |
