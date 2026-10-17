/** Hand-written mirrors of `api/schemas/`; keep the field names identical. */

/** ISO 8601, as FastAPI serialises `datetime`. */
export type Timestamp = string;

export interface User {
  id: string;
  email: string;
  name: string;
  created_at: Timestamp;
  email_verified_at: Timestamp | null;
  /** False until the emailed code is redeemed; login is refused until then. */
  is_active: boolean;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  /** Access token lifetime in seconds. */
  expires_in: number;
}

export interface AcceptedResponse {
  code: 'auth.verification_sent' | 'auth.verification_resent' | 'auth.reset_code_sent';
}

export interface PasswordResetGrant {
  /** Sets one new password; keep it in memory only. */
  reset_token: string;
  /** Seconds. */
  expires_in: number;
}

export type WorkspaceRole = 'owner' | 'editor' | 'viewer';

export interface Workspace {
  id: string;
  name: string;
  owner_id: string;
  created_at: Timestamp;
  /** The *calling* user's role, resolved server-side. */
  role: WorkspaceRole;
}

/** A membership row: the tie between a user and a workspace, plus their role. */
export interface Member {
  id: string;
  workspace_id: string;
  user_id: string;
  role: WorkspaceRole;
  created_at: Timestamp;
  user: User;
}

/** Structural, and assignable to Tiptap's `JSONContent` without a cast. */
export interface ProseMirrorNode {
  type?: string;
  attrs?: Record<string, unknown>;
  content?: ProseMirrorNode[];
  marks?: Array<{ type: string; attrs?: Record<string, unknown> }>;
  text?: string;
  [key: string]: unknown;
}

/** The backend rejects content whose `type` is not `doc`. */
export interface ProseMirrorDoc extends ProseMirrorNode {
  type: 'doc';
}

export interface DocumentSummary {
  id: string;
  workspace_id: string;
  title: string;
  version: number;
  /** Null once the author's account is deleted. */
  created_by: string | null;
  created_at: Timestamp;
  updated_at: Timestamp;
}

/** Named to avoid shadowing the DOM `Document` global. */
export interface DocumentDetail extends DocumentSummary {
  content: ProseMirrorDoc;
}

/** The 409 body from PATCH: the state the client has to re-sync to. */
export interface DocumentVersionConflict {
  code: string;
  current: DocumentDetail;
}

export type ErrorParams = Record<string, string | number>;

/** The API sends codes, never sentences: see api/core/exceptions.py. */
export interface ErrorBody {
  code?: string;
  params?: ErrorParams;
  /** On `request.invalid`: which field broke which rule. */
  fields?: Array<{ loc: Array<string | number>; type: string }>;
}

/* Real-time protocol: mirrors api/schemas/realtime.py and api/realtime/session.py. */

export interface WsTicket {
  ticket: string;
  expires_in: number;
}

export interface PeerPresence {
  user_id: string;
  name: string;
  color: string;
  /** Null until the peer has moved a cursor. */
  anchor: number | null;
  head: number | null;
}

/** `steps` come from an editor; `replace` and `title` from an HTTP PATCH. */
export interface DocumentOperation {
  steps?: unknown[];
  replace?: ProseMirrorDoc;
  title?: string;
}

export interface InitMessage {
  type: 'init';
  version: number;
  content: ProseMirrorDoc;
  title: string;
  peers: PeerPresence[];
  you: { user_id: string; name: string; color: string; role: WorkspaceRole };
}

export type ServerMessage =
  | InitMessage
  | { type: 'edit_ack'; version: number }
  | {
      type: 'edit';
      version: number;
      operation: DocumentOperation;
      user_id: string;
    }
  | { type: 'rejected'; server_version: number; content: ProseMirrorDoc }
  | ({ type: 'presence' } & PeerPresence)
  | { type: 'peer_left'; user_id: string }
  | { type: 'deleted'; user_id: string }
  | { type: 'pong' }
  | { type: 'error'; code: string };

export type ClientMessage =
  | {
      type: 'edit';
      base_version: number;
      /** Both, since the server cannot run ProseMirror. */
      operation: { steps: unknown[]; doc: ProseMirrorDoc };
    }
  | { type: 'cursor'; anchor: number; head: number }
  | { type: 'ping' };

/** Highest first, which is the order a role picker should offer them in. */
export const ROLES: readonly WorkspaceRole[] = ['owner', 'editor', 'viewer'];

export const ROLE_RANK: Record<WorkspaceRole, number> = {
  viewer: 1,
  editor: 2,
  owner: 3,
};

export function canEdit(role: WorkspaceRole): boolean {
  return ROLE_RANK[role] >= ROLE_RANK.editor;
}

/* AI assistant: mirrors api/schemas/ai.py and the events in api/services/ai_runner.py. */

/** `sources` asks, answered from the workspace's sources alone. */
export type AIQueryMode = 'continue' | 'rewrite' | 'summarize' | 'ask' | 'sources';

/** A [n] in a response, resolved by the server to the passage it was sent. */
export interface Citation {
  marker: number;
  chunk_id: string;
  source_id: string;
  filename: string;
  page_number: number | null;
  char_start: number;
  char_end: number;
  /** On a stored query: false once the source is deleted. Absent on a
   *  live stream, where it is always there. */
  available?: boolean;
}

export type AIQueryStatus = 'streaming' | 'completed' | 'cancelled' | 'failed';

export interface AIQuery {
  id: string;
  document_id: string;
  workspace_id: string;
  user_id: string | null;
  /** The user's instruction, not the assembled prompt. */
  prompt: string;
  mode: AIQueryMode;
  /** ProseMirror positions. For rewrite, snapped by the server to what apply replaces. */
  selection_from: number | null;
  selection_to: number | null;
  status: AIQueryStatus;
  response: string | null;
  error_code: string | null;
  model: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  created_at: Timestamp;
  completed_at: Timestamp | null;
  /** Passages sent with the prompt, [1] first. Null when the mode does not
   *  search sources; empty when the search found nothing. */
  retrieved_chunk_ids: string[] | null;
  citations: Citation[];
}

export interface AIQueryPage {
  items: AIQuery[];
  next_cursor: string | null;
}

export interface AIQueryInput {
  mode: AIQueryMode;
  instruction: string;
  selection_from?: number;
  selection_to?: number;
}

export interface AIUsage {
  prompt_tokens: number;
  completion_tokens: number;
  model: string | null;
}

export type AIStreamEvent =
  | { type: 'token'; text: string }
  /** `response` is the whole text, authoritative over the tokens. */
  | { type: 'done'; response: string; usage: AIUsage; citations: Citation[] }
  | { type: 'error'; code: string }
  | { type: 'cancelled' };

export interface WorkspaceUsage {
  day: string;
  tokens_used: number;
  tokens_remaining: number;
  /** 0 means no daily limit. */
  daily_token_limit: number;
  streaming_queries: number;
  max_concurrent_queries: number;
  resets_at: Timestamp;
  storage_used_bytes: number;
  /** 0 means no quota. */
  storage_quota_bytes: number;
  /** Largest file an upload may be. */
  source_max_file_bytes: number;
  embedding_requests_used: number;
  /** 0 means no limit, as with a local embedding model. */
  embedding_daily_request_limit: number;
}

/* Sources: mirrors api/schemas/source.py and api/schemas/search.py. */

export type SourceStatus = 'pending' | 'processing' | 'ready' | 'failed';

export interface Source {
  id: string;
  workspace_id: string;
  uploaded_by: string | null;
  filename: string;
  /** Sniffed from the bytes by the server. */
  content_type: string;
  size_bytes: number;
  checksum: string;
  status: SourceStatus;
  /** An API error code; translated like any other. */
  error_code: string | null;
  /** Failed for a reason another try may fix, such as a quota. */
  retryable: boolean;
  page_count: number | null;
  chunk_count: number;
  created_at: Timestamp;
  processed_at: Timestamp | null;
}

export interface SourceChunk {
  id: string;
  source_id: string;
  filename: string;
  chunk_index: number;
  content: string;
  page_number: number | null;
  char_start: number;
  char_end: number;
}
