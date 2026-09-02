# Synapse Canvas — Backend

Backend foundation for a real-time collaborative research assistant.

**Part 1 scope:** project skeleton, database layer, authentication, and
workspace management. Documents, WebSockets and AI are deliberately absent;
the structure below is arranged so they can be added without rework.

---

## Stack

| Concern        | Choice                                      |
| -------------- | ------------------------------------------- |
| Runtime        | Python 3.11+ (developed on 3.13)            |
| Web framework  | FastAPI                                     |
| ORM            | SQLAlchemy 2.0, async (`asyncpg`)           |
| Migrations     | Alembic (async `env.py`)                    |
| Database       | PostgreSQL 16+                              |
| Validation     | Pydantic v2 + pydantic-settings             |
| Auth           | JWT (PyJWT) + bcrypt via passlib            |
| Tests          | pytest + pytest-asyncio + httpx `AsyncClient`|

---

## Layout

```
app/
├── core/            # settings, domain errors, hashing + JWT
├── db/              # declarative base, engine, session dependency
├── models/          # SQLAlchemy models (the only layer that knows SQL)
├── schemas/         # Pydantic request/response contracts
├── services/        # business logic; no FastAPI imports
├── routers/         # HTTP only: parse, delegate, serialise
├── dependencies.py  # session, current user, workspace permission checks
└── main.py          # app factory and wiring
alembic/             # migration environment + versions
tests/               # transactional integration tests
```

The dependency direction is strictly one-way:

```
routers → services → models
   ↓          ↓
schemas    core
```

`services/` never imports FastAPI. That is what makes the same logic callable
from the WebSocket handshake and background workers planned for later parts,
and it keeps HTTP status codes confined to a single translation point
(`app_error_handler` in `main.py`).

---

## Setup

Everything runs in Docker. The only host requirement is Docker itself, plus
Python if you want to run the tests or migrations from your machine.

### 1. Environment

```bash
cp .env.example .env
```

Then set `JWT_SECRET_KEY` in `.env`. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The defaults for everything else already match the compose stack. Database
URLs **must** use the `postgresql+asyncpg://` scheme — settings validation
rejects anything else rather than failing later with a confusing driver error.

### 2. Start the stack

```bash
docker compose up -d --build
```

That starts PostgreSQL, waits for its healthcheck, applies migrations, and
serves the API on <http://127.0.0.1:8000>. Interactive docs at `/docs`,
liveness at `/health`.

Postgres is published on host port **5433**, chosen so it does not collide
with a natively installed server already holding 5432.

Database only, if you would rather run the app from your host:

```bash
docker compose up -d postgres
```

### 3. Host-side tooling (tests and migrations)

The test suite runs on the host against the containerised database, so the
image stays free of dev dependencies.

```bash
python -m venv .venv
```

```bash
.venv/Scripts/activate
```

(macOS/Linux: `source .venv/bin/activate`)

```bash
pip install -r requirements-dev.txt
```

```bash
alembic upgrade head
```

```bash
uvicorn app.main:app --reload
```

`.env` points host-side tooling at `localhost:5433`. Inside the compose
network the API reaches the database as `postgres:5432` instead, which
`docker-compose.yml` sets directly — that is the only reason two different
URLs exist.

### Notes on the container setup

- The image is multi-stage and runs as a non-root user; the build toolchain
  stays in the builder stage.
- `docker-compose.yml` mounts `./app` and `./alembic` read-only and runs
  uvicorn with `--reload`, which is a development convenience. **Drop both
  mounts and `--reload` for a real deployment**, where the image should be
  immutable.
- The `api` service runs `alembic upgrade head` on start. That is fine for
  local work, but production should apply migrations as a separate, ordered
  step rather than from every replica racing on boot.

### Troubleshooting: image pulls failing

If `docker compose build` or `up` fails with:

```
failed to do request: Head "https://registry-1.docker.io/v2/...":
tls: server did not echo the legacy session ID
```

then something on the network path is terminating TLS to Docker Hub — a
corporate proxy, a VPN, or antivirus HTTPS scanning. It is not a problem with
the Dockerfile. Options: disable HTTPS inspection for `*.docker.io`, point
the daemon at a registry mirror in Docker Desktop's settings, or `docker save`
/ `docker load` the base images from a machine that can reach the registry.

`POSTGRES_IMAGE_TAG` defaults to `15-alpine` for this reason: it is the newest
Postgres image already cached locally. Nothing in the schema needs a newer
server, so raise it whenever pulls work again.

---

## Tests

```bash
pytest
```

The suite needs a reachable PostgreSQL. It **creates `TEST_DATABASE_URL`'s
database if missing, then drops and recreates every table in it** — point it
at a throwaway database, never your development one.

### How isolation works

Each test runs inside an outer transaction that is never committed:

```python
connection = await engine.connect()
transaction = await connection.begin()      # rolled back at teardown
session = AsyncSession(bind=connection,
                       join_transaction_mode="create_savepoint")
```

`join_transaction_mode="create_savepoint"` makes the session open a SAVEPOINT
rather than a real transaction, so `commit()` inside application code releases
that savepoint instead of committing. The application therefore exercises its
real commit path — including `IntegrityError` handling that only fires against
a genuine unique index — while teardown discards everything with one
`ROLLBACK`. Faster than truncating tables, and tests stay order-independent.

The test client is bound to that same session by a dependency override, so
rows a test creates directly are visible to the endpoints under test.

Schema is built with `metadata.create_all` rather than by running migrations,
which keeps the suite fast. The trade-off is that migration drift is not
caught by `pytest`; `alembic upgrade head` on a clean database is the check
for that, and it is worth wiring into CI.

---

## API

All endpoints return errors as `{"detail": "..."}`.

### Auth

| Method | Path             | Notes                                        |
| ------ | ---------------- | -------------------------------------------- |
| POST   | `/auth/register` | 201 with the public user; 409 on duplicate    |
| POST   | `/auth/login`    | Returns an access + refresh pair              |
| POST   | `/auth/refresh`  | Exchanges a refresh token for a new pair      |
| GET    | `/auth/me`       | The authenticated user                        |

Access tokens live 30 minutes, refresh tokens 7 days. Send the access token as
`Authorization: Bearer <token>`.

### Workspaces

| Method | Path                                     | Minimum role |
| ------ | ---------------------------------------- | ------------ |
| POST   | `/workspaces`                            | authenticated |
| GET    | `/workspaces`                            | authenticated |
| GET    | `/workspaces/{id}`                       | viewer        |
| PATCH  | `/workspaces/{id}`                       | owner         |
| DELETE | `/workspaces/{id}`                       | owner         |
| GET    | `/workspaces/{id}/members`               | viewer        |
| POST   | `/workspaces/{id}/members`               | owner         |
| DELETE | `/workspaces/{id}/members/{user_id}`     | owner         |

`GET /workspaces/{id}/members` is not in the original endpoint list; it was
added because `DELETE .../members/{user_id}` is unusable without a way to
discover member ids.

---

## Design notes

**Permissions are a dependency, not a helper call.** `WorkspaceAccess` resolves
the caller's role and enforces a minimum in one place:

```python
@router.patch("/{workspace_id}")
async def update_workspace(payload: WorkspaceUpdate, ctx: RequireOwner, db: DbSession):
    ...
```

The handler contains no permission code. Better still, the dependency is also
the handler's only source of the workspace object — so forgetting the check
does not silently open a hole, it breaks the handler.

**404, not 403, for non-members.** The lookup is a single inner join across
`workspaces` and `workspace_members`. A workspace that does not exist and one
the caller cannot see both produce no row, so the two cases are
indistinguishable *inside* the service, not merely masked at the edge. A
member with insufficient rank gets a truthful 403, since they already know the
workspace exists. Tests assert the two 404 bodies are byte-identical.

**The owner is a real membership row.** `Workspace.owner_id` records who owns
it, but the owner also gets a `WorkspaceMember` row with `role=owner`. One
table answers every permission question, with no special case.

**Password hashing runs in a threadpool.** bcrypt at cost 12 is a few hundred
milliseconds of CPU. Called directly from a coroutine it would block the event
loop for every concurrent request; `anyio.to_thread.run_sync` moves it off,
and bcrypt releases the GIL so this genuinely parallelises.

**Tokens carry a `type` claim.** Without it a 7-day refresh token would be
accepted anywhere a 30-minute access token is, quietly erasing the short
access-token lifetime. `decode_token` requires the expected type.

**Login timing is equalised.** An unknown email still pays for one bcrypt
verification, so response time does not disclose which accounts exist.

**Passwords are capped at 72 bytes.** bcrypt silently truncates past that; a
user who chose a 100-character passphrase would otherwise be protected by only
its first 72 bytes.

**Emails are normalised once, in the service.** That is what makes the unique
index a real guarantee rather than one mixed case slips past.

### Known dependency quirk

`bcrypt` is pinned `<5`. passlib 1.7.4 reads `bcrypt.__about__.__version__`
during backend detection; bcrypt removed that in 4.1. On 4.x it is a logged
warning only (silenced in `core/config.py`) and hashing is unaffected — on 5.x
the backend probe fails outright.

---

## What I would do differently at production scale

- **Refresh tokens are stateless.** Nothing can revoke one before it expires,
  so logout is client-side only and a stolen refresh token is good for seven
  days. Production wants them persisted (or their `jti` denylisted), rotated
  on every use, with reuse detection to invalidate the family. The `jti` claim
  is already issued so this can be added without invalidating live tokens.
- **`HS256` with one shared secret.** Any service that validates tokens can
  also mint them. `RS256`/`EdDSA` with a published JWKS splits those
  capabilities and makes key rotation possible.
- **No rate limiting.** `/auth/login` and `/auth/register` are open to
  credential stuffing and enumeration by volume. Needs a per-IP and
  per-account limiter, plus lockout/backoff.
- **`GET /workspaces` is unpaginated.** Fine for tens of workspaces, not for
  thousands. Wants keyset pagination before that becomes a problem.
- **Native PostgreSQL enum for `role`.** Adding a role later means
  `ALTER TYPE ... ADD VALUE`, which has awkward transactional semantics. A
  `text` column plus a check constraint, or a lookup table, is easier to
  evolve.
- **Case-insensitive email by convention.** Normalisation is enforced in the
  service layer, so anything writing to the table by another path could break
  it. `citext`, or a unique index on `lower(email)`, moves the guarantee into
  the database.
- **Ownership cannot be transferred**, and deleting a user cascades away every
  workspace they own — including collaborators' data. Real systems need
  transfer, plus soft deletion and an audit trail.
- **No structured logging, tracing or request ids.** Needed before any of this
  is debuggable in production.
- **Schema built by `create_all` in tests.** Migrations should be applied to a
  scratch database in CI so drift between models and `alembic/versions` fails
  the build.
