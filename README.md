# Collaborative Research Assistant

A real-time collaborative workspace where teams write documents together, upload source
material, and query an LLM that answers with citations from those sources.

**Part 5 of 6** — auth, workspaces, documents edited live over a WebSocket, an AI
assistant whose responses stream over server-sent events and are inserted as
ordinary versioned edits, and uploaded sources it answers from with citations.
Password recovery by emailed code. A word-processor toolbar, Word import
and export, and a viewer that opens a citation at the chunk it cites.
English and Russian. 515 backend tests and 65 client tests.

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
| Client | React 18, TypeScript, Vite; react-pdf for the source viewer |
| Routing | React Router |
| Server state | TanStack Query |
| Editor | Tiptap 3 |
| LLM | Gemini via `google-genai`, or a scripted fake |
| Embeddings | `gemini-embedding-001`, or a local sentence-transformers model |
| File parsing | pypdf, python-docx (also Word import and export) |
| Tests | pytest, httpx; Vitest and jsdom for the client |

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
| Client tests | `npm test --prefix web` |


## Editor

Editors and owners get a toolbar over the document: text style, font and
size, bold, italic, underline, strikethrough, inline code, superscript and
subscript, text and highlight colour, links, alignment, line spacing and
indent, bulleted, numbered and check lists (nested with Tab or the indent
button), quotes, code blocks, horizontal lines, tables and page breaks.
While the cursor is in a table, a table group adds and removes rows and
columns, merges and splits cells and toggles the header row. On a narrow
screen the groups that do not fit move into a **More** menu. The keyboard
button, or Ctrl+/ (⌘/ on a Mac), lists every shortcut; Ctrl+Enter inserts a
page break.

Everything is a Tiptap extension from the official set, except two of our
own: the page break, an atomic node that docx export turns into a real page
break, and indent and line height, which are paragraph attributes as Word
keeps them (Tiptap's own line height marks the text instead).

### Editor schema versions

Every node, mark and attribute is listed in
[`web/src/editor/schema.json`](web/src/editor/schema.json) under a version
number, and `SCHEMA_VERSION` in `api/services/prosemirror.py` must match it;
the client tests fail when the extensions and the list disagree, and the
API tests when the two numbers do. **Changing the schema means bumping
both.**

A client declares its version when it opens a document's socket. The
server refuses any other version, or none, before sending the document:
an outdated tab would otherwise replay steps it cannot parse, or drop the
nodes it does not know and save the document back without them. The
client also checks the version in `init`, so a new client talking to an
old server stops too. Either way the page offers a reload, and nothing is
applied or sent. Documents saved under an older version still open: new
attributes take their defaults.

## Word import and export

**Export** — every document has an **Export to Word** button (any role):
`GET /workspaces/{id}/documents/{document_id}/export` answers with the
`.docx` itself, named after the title. **Import** — editors and owners
choose **Import Word file** on the workspace page:
`POST /workspaces/{id}/documents/import` reads the file and creates a new
document named after it, and answers with the document and `dropped`, the
codes of whatever the file held that the editor cannot; the page then says
in the reader's language what was left out.

Both run inside the request, on a worker thread: at the sizes allowed
(`DOCX_IMPORT_MAX_BYTES`, 10 MiB) that takes well under a second. A very
large document would want the Arq worker and a download link instead.

Only `.docx` is read. A Word 97-2003 `.doc` is refused with
`import.legacy_doc` and a hint to save it as `.docx`; nothing converts
it. The type comes from the bytes, not the name: a `.doc` and a
password-protected `.docx` share one container, and the streams inside it
tell them apart (`import.encrypted`). Damaged files are `import.corrupt`;
the ZIP checks are the same as for uploaded sources.

The importer walks the document's XML itself. mammoth, which turns a
`.docx` into clean HTML, was the alternative, but by design it leaves out
alignment, colour, fonts, sizes, highlight, spacing and indent -- half of
what export writes -- and python-docx's own paragraph and run objects skip
content controls and tracked insertions, losing their text.

### What survives the trip

A document exported and imported again comes back identical. From other
Word documents:

| | Export writes | Import reads |
|---|---|---|
| Headings 1–6 | Heading 1–6 styles | Heading styles; Title as heading 1. Heading numbers are **dropped** |
| Alignment, indent | Paragraph alignment; half an inch per indent level | Same; indents round to the nearest level, up to 8 |
| Line spacing | As a multiple | Multiples; exact and at-least spacing **dropped** |
| Bold, italic, underline, strikethrough, superscript, subscript | Run formatting | Same, also from character styles; double strikethrough becomes single |
| Inline code | An "Inline Code" character style | That style |
| Text colour, font, size | Run formatting | Same; theme fonts and colours are not resolved |
| Highlight | Shading, in any colour | Shading, or Word's 16 highlight colours |
| Links | Hyperlinks | Web and mail links; others keep their text only |
| Bulleted and numbered lists, nested | A numbering definition per list, so each restarts | Levels, formats (numbers, letters, roman), starts, continued numbering, and Word's "List Bullet 2"-style names |
| Checklists | Bulleted items opening ☐ or ☒ | Those, and Word's checkbox content controls |
| Quotes, code blocks | Quote and "Code Block" styles | Those and Word's similar styles |
| Horizontal line | An empty paragraph with a bottom border | The same, as Word's own `---` makes |
| Tables | Grid style, merged cells, a repeating header row | Merges both ways and the header row; ragged rows are padded |
| Page break | A real page break | Page breaks and "page break before"; a section break becomes a page break and is reported |
| Pictures, text boxes, equations, embedded objects, footnotes, comments, headers and footers, hidden text, column breaks, text effects | — | **Dropped**, and named |
| Tracked changes | — | Insertions kept, deletions removed, and reported |
| Page size, margins, paragraph spacing, first-line indent, custom styles | Word's defaults | Not read and not reported: page layout, which the editor does not model |

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
markers become buttons once it is done; each opens the chunk it cites, with
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
   cuts it into chunks of about `CHUNK_TARGET_TOKENS` at paragraph, sentence,
   line and only then word boundaries, embeds them in batches and stores them
   with their page and character offsets.
3. The source turns `ready`, or `failed` with a reason the panel words for
   itself. A used-up quota is worth retrying later and offers a retry; a
   damaged file, a password-protected one, or a scan with no text layer (OCR
   is not supported) is not, and says so.

The job can run twice, or crash half-way, without duplicating a chunk or
paying for one again: under a per-source lock it keeps the chunks it would
produce identically, deletes the rest before embedding what is missing, and
commits after each batch. Redis keeps nothing across a restart, so the worker
also queues again, once a minute, any source left pending.

### The source viewer

Every source in the Sources panel opens in a side panel, and so does every
citation in an answer: at the cited chunk, scrolled into view and
highlighted. A PDF is drawn page by page (react-pdf, loaded only when one
is opened) and opens on the cited page; plain text and Markdown are shown
as the text the chunks were cut from, so their offsets point exactly; a
Word file is shown as the server renders it. The panel pages, zooms,
searches the whole file and downloads the original, and lists the
**indexed chunks** -- what the AI reads of the file -- tinted in the
text, so a user can see what was left out. A kind of file it cannot show,
or a PDF that will not open, is offered as a download instead.

`GET /workspaces/{id}/sources/{source_id}/content` streams the file as
uploaded, for any member: its sniffed type (text with the charset it was
read in), shown inline, with a sandboxing CSP. `GET .../text` returns the
extracted text, page starts and every chunk's offsets, and for a Word
file escaped HTML built by the server. Both go through the same
membership check as everything else; no storage path or static URL ever
reaches the client, which would let a link outlive a revoked membership.

The worker now keeps the text it extracts, in `source_texts`. A source
processed before that has none; the first time it is opened it is read
again, in the worker's isolated parser, and kept.

A PDF's chunks come from pypdf on the server and its pages from pdf.js
in the browser, which space text differently, and a Word file's rendering
joins blocks differently from its extracted text: in both, chunks are
found by their words, ignoring whitespace, case and the bars between
extracted table cells, and a chunk running onto the next page is
followed there. Highlights use the CSS Custom Highlight API; a browser
without it still scrolls to the chunk, unmarked.
