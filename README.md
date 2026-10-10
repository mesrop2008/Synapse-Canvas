# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

**Part 4 of 6** — auth, workspaces, documents edited live over a WebSocket, and an
AI assistant whose responses stream over server-sent events and are inserted as
ordinary versioned edits. Password recovery by emailed code. English and Russian.
339 backend tests.

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

## Password recovery

**Forgot the password?** on the login page asks for an email, then a six-digit
code, then the new password twice. Codes come by email like verification
codes (or into the API log under `EMAIL_BACKEND=console`).

| | |
|---|---|
| `POST /auth/password-reset` | `{email}` → always 202, the same body for any address |
| `POST /auth/password-reset/verify` | `{email, code}` → `{reset_token, expires_in}` |
| `POST /auth/password-reset/confirm` | `{reset_token, new_password}` → 204 |

| Rule | Default |
|---|---|
| Code lifetime | 5 min |
| Resend | once a minute; a new code voids the old one |
| Wrong codes | the third destroys the code and locks the address for 5 min (`auth.reset_locked`) |
| Across codes | 20 wrong codes per address a day |
| Reset token | a JWT of its own type, 5 min, one use |

Settings are under *Password recovery* in [`.env.example`](.env.example).

- **Nothing reveals an account.** Unknown and unconfirmed addresses get the
  same 202, the same cooldown and the same lockout, and their wrong codes
  count the same. Only the mail differs, and only the inbox sees it.
- **Redis, not a table.** Code, attempts, cooldown and lock all live minutes,
  so they are keys that expire on their own. Only a keyed hash of the code is
  stored. A flush voids outstanding codes and lifts locks.
- **One version voids every token.** `users.token_version` is stamped into
  access, refresh and reset tokens and checked on every request. The reset
  bumps it in the same compare-and-set that writes the password. That spends
  the reset token, any other reset token, and every session on every device;
  access tokens die at once, not 30 minutes later. Tokens from before the
  column count as version 0, so the upgrade signed nobody out.
- After each reset the owner is emailed that the password changed, which is how
  they find out if it was not them.

### Before production

- Close open WebSockets on a version bump; today they stay open until they
  reconnect, which then fails.
- Bump the version on **sign out everywhere** too, so it ends access tokens
  at once.
- A lockout keyed on an address lets anyone who knows it pause that
  account's recovery for 5 minutes at a time. Login is unaffected.

## Roadmap

| Part | |
|---|---|
| 5 | File upload and pgvector retrieval |
| 6 | Hardening and deployment |
