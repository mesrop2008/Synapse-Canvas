# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

Multiple people can edit the same document simultaneously, see each other's cursors, and
watch AI responses stream in live. The technically interesting part is keeping several
browser clients consistent with each other and with the server while edits, presence
updates, and token streams all flow at once.

**Status:** Part 2 of 6 complete — authentication and workspaces (Part 1), then documents
with optimistic concurrency and a React client with a Tiptap editor that autosaves.
144 tests, 85% line coverage on the backend.

One user can now create a workspace, create a document, type, refresh the page, and find
their text intact.

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI, Python 3.11+ |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Database | PostgreSQL |
| Validation | Pydantic v2 |
| Auth | JWT (access + refresh), bcrypt via passlib |
| Backend tests | pytest, httpx AsyncClient |
| Client | React 18 + TypeScript, Vite |
| Routing | React Router (data router) |
| Server state | TanStack Query |
| Editor | Tiptap 3 (ProseMirror) |
| Styling | Plain CSS, one stylesheet |

Planned for later parts: Redis, pgvector, Celery or Arq.

## Repository layout

```
api/            FastAPI application
  core/         config, security, logging, middleware, domain exceptions
  db/           engine, session, declarative base
  models/       SQLAlchemy models
  schemas/      Pydantic request/response models
  services/     business logic — no FastAPI imports
  routers/      HTTP endpoints; translation only
alembic/        migrations
tests/          pytest suite, runs against a real PostgreSQL
web/            React client
  src/api/      typed fetch client, one module per resource
  src/hooks/    auth context, TanStack Query hooks, autosave
  src/pages/    one component per route
  src/components/
  src/types/    hand-written mirrors of the API schemas
```

Services hold the logic and import nothing from FastAPI, so Part 3's WebSocket handlers can
call the same functions a route handler does. A handler cannot usefully raise an
`HTTPException`, so failures are domain exceptions from `api/core/exceptions.py` that a
single place in `api/main.py` maps to status codes.

## Running locally

You need Docker (for PostgreSQL) and Node 20+.

### 1. The backend

```bash
cp .env.example .env
```

```bash
docker compose up -d --build
```

That is the whole backend setup. `.env` ships with `JWT_SECRET_KEY` blank and the container
generates one on first run, persisting it to a volume.

Compose starts PostgreSQL, waits for its healthcheck, runs `alembic upgrade head` to
completion, and only then starts the API on <http://localhost:8000>. Postgres is published on
host port **5433** so it does not collide with a natively installed server on 5432.

Seeing the `migrate` container as `Exited (0)` afterwards is the success case, not a crash.

### 2. The web client

```bash
cd web && cp .env.example .env && npm install
```

```bash
npm run dev
```

<http://localhost:5173>. The dev server's port is fixed, because it has to match an entry in
the API's `CORS_ORIGINS` and the verification link base.

### Running the API on the host instead

Useful for debugging, and required for the test suite:

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
uvicorn api.main:app --reload
```

### Creating an account

A new account must verify its email before it can log in. Nothing is actually sent locally —
the console "sender" logs the message, link included, to the API log.

1. Register at <http://localhost:5173/register>
2. Find the link in the API output (`docker compose logs api`, or the terminal running
   uvicorn). Look for `[email:console]`
3. Open it. It lands on `/verify-email`, which redeems the token
4. Sign in

If you are running uvicorn through a process that swallows application log records, the
quickest way past step 2 is to set `email_verified_at` directly:

```bash
docker compose exec postgres psql -U synapse -d synapse_canvas -c "UPDATE users SET email_verified_at = now() WHERE email = 'you@example.com';"
```

### Tests

```bash
pytest
```

```bash
pytest --cov=api
```

Tests run against a real PostgreSQL, each inside a transaction that is rolled back
afterwards, so they are order-independent and leave no rows behind. `TEST_DATABASE_URL` must
point at a throwaway database — the suite drops every table in it.

The web client has no test suite yet; `npm run build` typechecks it.

## Environment variables

### Backend (`.env`)

Everything in `.env.example` is commented. The ones that matter for Part 2:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `…@localhost:5433/synapse_canvas` | Must use the `postgresql+asyncpg://` driver; settings validation rejects anything else |
| `TEST_DATABASE_URL` | `…/synapse_canvas_test` | Throwaway. The suite drops every table in it |
| `JWT_SECRET_KEY` | *(blank)* | Generated on first container run. Required outside local/test |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Comma-separated. Empty disables the CORS middleware entirely. `*` is refused at startup, because Starlette pairs a wildcard with credentials by echoing the caller's origin |
| `EMAIL_VERIFICATION_LINK_BASE` | `http://localhost:5173/verify-email` | Must point at the client's route, or the token never reaches the API |
| `MAX_REQUEST_BODY_BYTES` | `1048576` | Caps a document body too — a 1 MiB PATCH is refused before it is read |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | |

### Client (`web/.env`)

Vite only exposes variables prefixed with `VITE_`, and inlines them at build time — so these
are public, and nothing secret belongs here.

| Variable | Default | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | No trailing slash |

## The documents API

All routes are scoped to a workspace and go through the Part 1 role dependency.

| Method | Path | Role |
|---|---|---|
| `POST` | `/workspaces/{workspace_id}/documents` | editor+ |
| `GET` | `/workspaces/{workspace_id}/documents` | viewer+ (no content bodies) |
| `GET` | `/workspaces/{workspace_id}/documents/{document_id}` | viewer+ |
| `PATCH` | `/workspaces/{workspace_id}/documents/{document_id}` | editor+ |
| `DELETE` | `/workspaces/{workspace_id}/documents/{document_id}` | editor+ |

A non-member gets **404**, never 403: the workspace's existence must not leak to a stranger.
A member whose role is too low gets **403**, because they already know it exists.

### Optimistic concurrency

`PATCH` requires the `version` the edit was computed against:

```json
{ "version": 4, "title": "optional", "content": { "type": "doc", "content": [] } }
```

A match increments the version and returns the new row. A mismatch returns **409** with the
server's current state, so the loser of a race can re-sync from the response instead of
issuing another `GET`:

```json
{
  "detail": "Document has been modified since you loaded it",
  "current": { "id": "…", "version": 5, "title": "…", "content": { "type": "doc", … } }
}
```

The check lives in the `UPDATE`'s `WHERE` clause rather than in a preceding `SELECT`, so there
is no window between reading the version and writing it:

```sql
UPDATE documents SET …, version = version + 1
 WHERE id = :id AND workspace_id = :ws AND version = :expected
```

Two concurrent updates holding the same version serialise on the row lock. The loser
re-evaluates the predicate against the winner's committed row, matches nothing, and is told it
is stale. A `SELECT`-then-`UPDATE` would let both write.
`tests/test_documents.py::test_concurrent_patches_cannot_both_be_applied` races two real
connections over one row to prove it.

## Notes on the design

Things that are not obvious from reading the code.

**Content is ProseMirror JSON in `JSONB`, not HTML.** Part 3 has to apply edits at the node
level and Part 5 has to walk the tree to chunk it for embedding; neither is tractable against
a serialised HTML string. The server otherwise treats the body as opaque, with one exception:
it rejects content whose `type` is not `"doc"`, which is cheap and stops a write that would
store a document no editor can open.

**`created_by` is `ON DELETE SET NULL`.** Deleting an account must not delete documents it
contributed to a workspace that outlives it. The column is therefore nullable, and the API
returns `created_by: null` for an orphaned document.

**`updated_at` uses `clock_timestamp()` on update, not `now()`.** `now()` is the *transaction*
timestamp, so two writes in one transaction record the same instant and leave the list order
arbitrary between them. The list also breaks ties on `id`, making the order total — pagination
will need that.

**The 409 payload is a detached snapshot, not the ORM row.** The exception outlives its
session: as it unwinds, the request's transaction is rolled back, which expires every object
loaded in it. An attached row raised `DetachedInstanceError` the moment the exception handler
tried to serialise it, and the endpoint answered 500 instead of 409. The test suite could not
see this, because the client fixture overrides `get_db` with a session that is never closed.

**Concurrent 401s share one refresh.** Several queries expiring together is the normal case;
one refresh each would mint several token pairs and race each other writing localStorage. The
first caller stores its promise and the rest await it. Restoring a session on reload falls out
of the same path for free: the access token is memory-only, so the first request after a
reload goes out unauthenticated, 401s, refreshes, and replays.

**The editor mounts only after its content has loaded.** Loading into a live editor would
mean distinguishing that write from a user edit; mounting with the content already in hand
avoids the question. The same reason drives `emitUpdate: false` on the conflict reload —
without it, replacing the content would look like an edit and schedule a save of the server's
own content straight back to it.

**Autosave sends title and content together.** They share one version, so two requests would
have the second racing the version the first just produced.

**A save in flight counts as unsaved.** The request can still fail, so the navigation guards
treat `saving` as dirty.

## What I would change for production

- **Refresh token in an httpOnly cookie.** It currently lives in localStorage, which any
  script injected into the origin can read — an XSS becomes a stolen long-lived session. The
  production answer is an httpOnly, Secure, SameSite cookie set by the backend, plus a CSRF
  token. This is a deliberate Part 2 simplification to avoid cookie plumbing; the comment in
  `web/src/api/client.ts` says so at the point it matters.
- **Last-write-wins is not good enough.** On 409 the server wins outright and the user's text
  is only recoverable from a console warning. That is honest for Part 2 but not shippable;
  Part 3 replaces it with a change log and a rebase, and the console log is the placeholder
  for the merge UI.
- **Real email delivery.** `ConsoleEmailSender` logs the verification link instead of sending
  it, which is a leak anywhere but a laptop. The `EmailSender` protocol exists so swapping in
  SES or Postmark is one class.
- **Code-split the editor route.** Tiptap and ProseMirror push the bundle past 500 kB, and
  every page pays for it including login. A lazy route for the editor would fix that.
- **Bump `MAX_REQUEST_BODY_BYTES`, or stop sending whole documents.** Autosave PATCHes the
  entire document every time, so a long document hits the 1 MiB cap and, before that, wastes
  bandwidth on every keystroke pause. Part 3's diffs make this moot.
- **Paginate the document list.** It returns every document in a workspace. Summaries are
  small and the index already supports a total order, so this is just unwritten.
- **A client test suite.** The refresh single-flight, the debounce, and the conflict reload
  are the parts most likely to break silently, and they have no automated coverage — they were
  verified by hand against a running stack.
- **Retire the CSP exemption for `/docs`.** Swagger loads assets from a CDN. Fine locally;
  in production the docs should either be off or served with vendored assets.

## Roadmap

### Part 3 — Real-time sync

The hard part. A WebSocket endpoint per document, a connection manager, and version-tracked
edits in an append-only `DocumentChange` log that becomes the source of truth for sync and,
later, history.

Redis pub/sub broadcasts edits across backend processes — without it, two users on different
worker processes cannot see each other, since each process holds its own in-memory set of
connections.

Conflict handling starts as reject-and-rebase: the server holds the authoritative version
number, and a client whose edit carries a stale version refetches and reapplies. That is the
409 path from Part 2, moved onto the socket. Operational transform is the follow-up, once the
simpler approach works and its failure modes are understood. Presence and remote cursors ship
here too.

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
