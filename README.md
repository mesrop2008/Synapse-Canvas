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

## Real-time editing

### The write path

Every accepted edit appends a row to `document_changes` and moves
`documents.content` and `documents.version` in the same transaction. The log is
the history; the document row is its materialised head.

One function does the writing, `services.documents.apply_change`:

```
SELECT ... FOR UPDATE      lock this document's row
version == base_version?   no  -> roll back, raise StaleDocumentVersionError
                           yes -> append the change, apply it, bump the version
COMMIT
```

The row lock is the load-bearing decision. It serialises every writer of one
document between reading the version and committing the next one, which is what
makes "exactly one of two simultaneous edits wins" a guarantee rather than a
likelihood. Locks are per row, so different documents never wait on each other:
concurrency is bounded by how many people are in one document, not by how busy
the deployment is. The unique constraint on `(document_id, version)` is the
database's own backstop underneath it.

### What the HTTP PATCH does now

Part 2's `PATCH /workspaces/{id}/documents/{id}` is **not** a second write path.
It calls the same locked function, lands in the same log, and consumes a version
like any other edit, then publishes that version to the document's channel so
anyone with the document open hears about it instead of having their next
keystroke rejected for a change they were never shown.

The client uses it for renames only; the body goes over the socket. A title is a
single small field with no merge problem, and routing it over HTTP keeps the
socket protocol to one kind of message.

### Operations

An operation is what one version *did*: stored in the log, replayed by peers.

| Shape | From |
|---|---|
| `{"steps": [...]}` | a live editor |
| `{"replace": <doc>}` | an HTTP content PATCH |
| `{"title": "..."}` | an HTTP rename |

A client sends `{"steps": [...], "doc": {...}}` -- the steps for peers, the
document for the server. The server cannot derive one from the other, because
ProseMirror is a JavaScript library, so the client sends both; taking its word
for the result is safe only because `base_version` had to match exactly, which
means it computed that result from the same bytes the server holds. The `doc` is
stripped before the operation is logged and broadcast.

### Reject and rebase

An edit whose `base_version` is stale is refused with `rejected`, carrying the
server's version and its full content. The client replaces its state with that.

**In-flight local edits are lost.** Keystrokes typed in the moment between two
people saving are simply gone: they were never transformed onto the version that
won, and nothing rebases them. Operational transform is the follow-up -- transform
the losing steps against the winning ones and apply both. It is a larger piece of
work than it looks, because the transform has to run identically on both sides.

### Connections and Redis

Each Uvicorn worker holds its own registry of `document_id -> connections`, so
two people in one document are usually not in the same process. Outbound messages
go out through Redis (`doc:{document_id}`) and come back to every worker, which
delivers to whichever of its own sockets are in that document. Each message names
the connection that caused it, and the relay skips that one: it was already
acknowledged directly.

One subscriber task per process, on a pattern subscription rather than a channel
per open document. A single subscription needs no coordination with the reader
task, whereas subscribing while that reader is blocked on the same connection
does. The cost is that a worker receives traffic for documents it holds no
connections for, and discards it.

### Authentication

A browser cannot set an `Authorization` header on a WebSocket, and the usual
workaround -- the access token in the query string -- writes a thirty-minute
credential into every access log and proxy trace along the way. So
`POST /documents/{id}/ws-ticket` mints a single-use ticket that lives about
thirty seconds, stored in Redis and redeemed with `GETDEL`. The ticket proves
identity only: role and membership are re-read from the database on connect, so
access revoked in between is honoured. Viewers connect and receive everything;
their edits are refused without closing the socket.

### Presence

Presence and cursors live in Redis and never in Postgres -- one hash per document,
keyed by user, refreshed on every heartbeat. Redis expires keys rather than hash
fields, so each entry carries its own expiry and readers sweep what has lapsed;
that is what clears a peer who dropped without closing. Colours are derived from
the user id, so the same person is the same colour everywhere, with nothing to
allocate or reconcile.

### Protocol

Client to server:

```json
{"type": "edit", "base_version": 42, "operation": {"steps": [], "doc": {}}}
{"type": "cursor", "anchor": 120, "head": 125}
{"type": "ping"}
```

Server to client:

```json
{"type": "init", "version": 42, "content": {}, "title": "", "peers": [], "you": {}}
{"type": "edit_ack", "version": 43}
{"type": "edit", "version": 43, "operation": {}, "user_id": ""}
{"type": "rejected", "server_version": 44, "content": {}}
{"type": "presence", "user_id": "", "name": "", "color": "", "anchor": 120, "head": 125}
{"type": "peer_left", "user_id": ""}
{"type": "deleted", "user_id": ""}
{"type": "error", "code": "forbidden", "detail": ""}
{"type": "pong"}
```

Close codes are in the application range: `4401` bad ticket, `4403` not
permitted, `4404` no such document, `4408` no heartbeat, `4429` too many edits,
plus `1009` for an oversized frame.

### The client

`useDocumentSocket` owns the connection. It fetches a ticket, connects, and
exposes `connecting | live | reconnecting | offline`. Reconnects use exponential
backoff with jitter and a fresh ticket each attempt, because the previous one was
spent on the socket that died. Every reconnect resyncs from `init`: local state is
never assumed to have survived the gap.

Outbound edits are batched for 250 ms and sent one at a time, since the server
accepts an edit only against the version it holds and a second on the wire would
be based on one the first is about to replace. Inbound steps are replayed only
when nothing local is outstanding: a peer's step positions were computed against
their own base, and replaying them over unsent local changes would leave this
copy agreeing with neither the server nor the peers. When that cannot be done, or
when a step will not apply, the client reconnects and takes the server's
document, with a non-destructive notice saying so.

Two states are terminal rather than retried. `deleted` arrives when an editor
or owner removes the document, which cascades its change log away with it;
`unavailable` is a 404 or 403 on the ticket, which covers both "no such
document" and "you are no longer a member" -- the API does not distinguish them,
on purpose. Neither is fixed by waiting, so the client stops and says which one
happened instead of reconnecting forever behind an "offline" badge.

**Editing is blocked while the socket is down**, rather than buffered. Buffered
edits would have to be rebased on reconnect, and reject-and-rebase has no rebase:
they would be collected, shown to the user as progress, then thrown away.
Refusing them up front loses the same keystrokes without pretending otherwise.

Remote cursors and selections are ProseMirror decorations in the peer's colour,
fed by a transaction rather than a React prop, because decorations are editor
state. Remote changes are applied behind a flag that suppresses the outbound
send, so a peer's edit is never echoed back as a local one.

## For production

| | |
|---|---|
| Per-edit payload | An edit carries the whole document. Run the ProseMirror schema server-side, in a Node sidecar, and send steps alone -- or move to a CRDT. |
| Reject and rebase | Replace it with operational transform, so concurrent edits merge instead of one being discarded. |
| Pub/sub fan-out | `doc:*` sends every document's traffic to every worker. Per-document subscriptions on a dedicated connection, or Redis streams, once the worker count justifies it. |
| Change log growth | Nothing prunes `document_changes`. Compact old versions behind periodic snapshots. |
| Multiple tabs | Presence is keyed by user, so closing one tab briefly clears another tab's entry until its next heartbeat. Key by connection, or delete with a Lua compare-and-delete. |
| Redis restarts | Presence and tickets are disposable, but a restart drops every subscription mid-flight. Clients recover on their next heartbeat; managed Redis with failover removes the gap. |
| Frame limits | `WS_MAX_MESSAGE_BYTES` is the application backstop. Cap frames at the proxy and with uvicorn's `--ws-max-size` as well. |

## Roadmap

| Part | |
|---|---|
| 4 | AI responses streamed over SSE |
| 5 | File upload and pgvector retrieval |
| 6 | Hardening and deployment |
