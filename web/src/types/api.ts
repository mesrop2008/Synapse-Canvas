/**
 * Hand-written mirrors of the schemas in `api/schemas/`. Nothing checks these
 * against the backend, so field names must stay byte-identical to the Python,
 * snake_case included.
 */

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
  detail: string;
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

/**
 * Structural rather than a union of node types: Tiptap owns the schema. Shaped
 * to stay assignable to Tiptap's `JSONContent` so content crosses that boundary
 * without a cast.
 */
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
  detail: string;
  current: DocumentDetail;
}

export interface ErrorBody {
  /** Stable identifier; see api/core/exceptions.py. Absent on responses the
   *  API did not generate itself. */
  code?: string;
  detail: unknown;
}

/* --- real-time protocol ---------------------------------------------------
   Mirrors api/schemas/realtime.py and the frames assembled in
   api/realtime/session.py. */

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

/**
 * What one accepted version did. `steps` come from a live editor; `replace` and
 * `title` from an HTTP PATCH, which cannot be expressed as steps.
 */
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
  | { type: 'error'; code: string; detail: string };

export type ClientMessage =
  | {
      type: 'edit';
      base_version: number;
      /** The steps peers replay, and the document they produced -- the server
       *  cannot run ProseMirror to derive one from the other. */
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
