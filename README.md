# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

Multiple people can edit the same document simultaneously, see each other's cursors, and
watch AI responses stream in live. The technically interesting part is keeping several
browser clients consistent with each other and with the server while edits, presence
updates, and token streams all flow at once.

**Status:** Part 1 of 6 complete — authentication, workspace management, and a security
hardening pass.

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

## What works today

### Authentication

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/auth/register` | Create an account and send a verification link |
| POST | `/auth/verify-email` | Redeem the emailed token |
| POST | `/auth/resend-verification` | Request a fresh link |
| POST | `/auth/login` | Exchange credentials for tokens |
| POST | `/auth/refresh` | Rotate the refresh token, get a new pair |
| POST | `/auth/logout` | End the session the refresh token belongs to |
| POST | `/auth/logout-all` | End every session for the current user |
| GET | `/auth/me` | Current user |

Access tokens live 30 minutes, refresh tokens 7 days. Passwords are hashed with bcrypt and
never stored or returned in plaintext.

**A new account must verify its email before it can log in or be added to a workspace.**
Locally no mail is sent — the verification link is written to the API log by the console
email sender in `app/services/email_service.py`.

### Workspaces

| Method | Endpoint | Required role |
|---|---|---|
| POST | `/workspaces` | any authenticated user |
| GET | `/workspaces` | any authenticated user (returns only your workspaces) |
| GET | `/workspaces/{id}` | viewer |
| PATCH | `/workspaces/{id}` | owner |
| DELETE | `/workspaces/{id}` | owner |
| GET | `/workspaces/{id}/members` | viewer |
| POST | `/workspaces/{id}/members` | owner |
| DELETE | `/workspaces/{id}/members/{user_id}` | owner |

Creating a workspace also creates the creator's `WorkspaceMember` row with the `owner` role,
so membership is never implicit — every permission check reads from one table.

### Permissions

A reusable FastAPI dependency resolves the caller's role for a given workspace and enforces a
minimum required role, keeping route handlers free of authorization branching.

Two deliberate behaviours:

- **Non-members get 404, not 403.** A 403 confirms the workspace exists, which leaks
  information to anyone probing IDs. A non-member gets the same response as for an ID that
  was never real. The lookup is a single inner join, so the two cases are indistinguishable
  inside the service rather than merely masked at the edge.
- **The owner cannot be removed from their own workspace.** Otherwise a workspace could end
  up with no one able to administer it.

### Security hardening

- **Revocable refresh tokens.** Each issued token is recorded server-side (only its `jti`),
  rotated on every refresh, and its whole family revoked on reuse — so a stolen-and-replayed
  token forces re-authentication instead of granting seven days of silent access.
- **Signing-key rotation.** Every token carries a `kid`; `PREVIOUS_JWT_SECRET_KEYS` keeps
  retired keys valid for verification only, so the active key can be replaced without logging
  everyone out. Tokens also pin `iss`/`aud`, rejecting one minted by another deployment.
- **Throttling on auth endpoints**, per IP and — for login — per targeted account. Throttling
  rather than lockout, because a lockout keyed on an account lets anyone lock its owner out.
- **Enumeration-resistant registration.** `/auth/register` answers `202` with an identical
  body whether or not the address existed, and hashes on both paths so the timing matches
  too. The address's real owner is notified by email instead.
- **Transport hardening.** Security headers and a strict CSP on every response, a request-body
  size cap, a catch-all handler so no stack trace reaches a client, and a refusal to boot with
  a wildcard CORS origin.

## Data model

```
User
  id (UUID, pk), email (unique), hashed_password, name
  email_verified_at (null until proven), created_at

Workspace
  id (UUID, pk), name, owner_id (FK -> User), created_at

WorkspaceMember
  id (UUID, pk), user_id (FK), workspace_id (FK)
  role: owner | editor | viewer
  created_at
  UNIQUE (user_id, workspace_id)

RefreshToken
  id (UUID, pk), jti (unique), user_id (FK), family_id
  expires_at, revoked_at, replaced_by_jti, created_at

EmailVerificationToken
  id (UUID, pk), token_hash (unique, SHA-256), user_id (FK)
  expires_at, used_at, created_at

RateLimitBucket
  id (UUID, pk), bucket_key, window_start, request_count
  UNIQUE (bucket_key, window_start)
```

Neither token table stores anything redeemable: refresh tokens are identified by `jti`
(the signature already proves authenticity) and verification tokens only by their SHA-256.

## Project layout

```
app/
  routers/          HTTP concerns only — parse, delegate, serialize
  services/         business logic, no FastAPI imports
  models/           SQLAlchemy models
  schemas/          Pydantic request/response models
  core/             config, security, middleware, logging, exceptions
  db/               session and engine setup
  dependencies.py   shared dependencies incl. the role gate
alembic/            migrations
tests/
docker-compose.yml
.env.example
```

Routers stay thin so that Part 3 can call the same service functions from WebSocket handlers
instead of HTTP routes, without duplicating logic.

## Running locally

```bash
cp .env.example .env
```

Then set `JWT_SECRET_KEY` in `.env` (minimum 32 characters):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Start PostgreSQL. It is published on host port **5433**, chosen so it does not collide with a
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

To run the whole stack (API included) in Docker instead: `docker compose up -d --build`.

There is no user interface yet. Explore the API at `http://localhost:8000/docs`, which serves
FastAPI's generated Swagger UI. Register a user, then **copy the verification link from the
API log and redeem it at `POST /auth/verify-email`** — login returns 403 until you do. Then
log in, click **Authorize**, paste the access token (no `Bearer` prefix), and call the
workspace endpoints.

```bash
pytest
```

```bash
pytest --cov=app
```

**109 tests, 84% line coverage.** They run against a real PostgreSQL, each inside an outer
transaction that is never committed — so the application's genuine commit path runs
(including `IntegrityError` against a live unique index) while teardown discards every row in
one rollback. Tests are therefore order-independent and leave nothing behind.

Coverage spans the full auth flow, workspace CRUD, email verification, refresh-token rotation
and reuse detection, rate limiting, transport hardening, and the permission boundaries
specifically: a viewer cannot rename a workspace, a non-member receives 404 with a body
identical to a nonexistent workspace, and the owner cannot be removed.

### Troubleshooting

If `docker compose` fails with `tls: server did not echo the legacy session ID`, something on
the network path (a proxy, VPN, or antivirus HTTPS scanning) is terminating TLS to Docker Hub.
`POSTGRES_IMAGE_TAG` in `.env` defaults to an image tag you may already have cached; nothing
in the schema needs a newer server.

## Roadmap

### Part 2 — Documents and the React shell

`Document` model with content stored as structured JSON rather than HTML, plus REST CRUD. A
React + TypeScript client with routing, the login flow wired to Part 1, and a Tiptap editor
that autosaves over plain HTTP. At the end of this part one user can create a document, type,
refresh, and find their text intact.

### Part 3 — Real-time sync

The hard part. A WebSocket endpoint per document, a connection manager, and version-tracked
edits recorded in an append-only `DocumentChange` log that becomes the source of truth for
sync and, later, history.

Redis pub/sub broadcasts edits across backend processes. Without it, two users connected to
different worker processes cannot see each other, since each process holds its own in-memory
set of connections.

Conflict handling starts as reject-and-rebase: the server holds the authoritative version
number, and a client whose edit is tagged with a stale version refetches and reapplies.
Operational transform is the follow-up once the simpler approach is working and its failure
modes are understood.

Presence and remote cursors ship in this part too.

### Part 4 — AI integration

Server-sent events for streaming LLM responses, keeping the AI stream separate from the edit
WebSocket. Prompt assembly from document context, `AIQuery` persistence, and a React UI that
renders tokens as they arrive.

### Part 5 — Retrieval

File upload with a background worker that chunks and embeds source documents into pgvector.
Semantic search over those chunks, and AI answers that cite the sources they drew from.

### Part 6 — Hardening

Per-workspace token and cost tracking, workspace invitations, real error and reconnection
states in the UI, and deployment. (Rate limiting and the auth hardening above landed early,
in Part 1.)

## Design notes

**Why an append-only change log instead of overwriting document content.** Storing every edit
as a row makes conflict resolution, undo, and per-user attribution fall out of the same
structure. Overwriting a content column would work for a single editor and then need
replacing entirely once a second one arrives.

**Why SSE for AI and WebSockets for edits.** Edits are bidirectional and ordered; AI streaming
is one-way and disposable. Splitting them keeps the edit protocol small and lets a dropped AI
stream reconnect without disturbing the editing session.

**Why role checks live in a dependency.** Authorization scattered across handlers is where
gaps appear. One dependency, used everywhere, means the rule is written once and tested once.
It is also the handler's only source of the workspace object, so forgetting the check breaks
the handler loudly rather than opening a hole quietly.

**Why email verification gates workspace membership.** Members are invited by email address,
so an account that never proved control of its address could inherit an invitation meant for
the real owner of that address.

## Known limitations

- `HS256` with a shared secret: key rotation works, but any service that validates tokens can
  also mint them. `RS256`/JWKS would split those capabilities.
- The rate limiter is a PostgreSQL table — correct and shared across replicas, but a write per
  throttled request. Redis or the API gateway is the right home at volume.
- Email delivery is a console stub behind an `EmailSender` protocol; a real provider is one
  class.
- `GET /workspaces` is unpaginated.
