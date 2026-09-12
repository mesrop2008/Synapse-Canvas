/**
 * Hand-written mirrors of the FastAPI response schemas in `app/schemas/`.
 *
 * Deliberately not generated: the surface is small, and a generator would have
 * to be re-run (and its output reviewed) on every backend change. When the two
 * drift, the compiler is the only thing that notices -- so keep field names
 * byte-identical to the Python, snake_case included.
 */

/** ISO 8601, as FastAPI serialises `datetime`. */
export type Timestamp = string;

export interface User {
  id: string;
  email: string;
  name: string;
  created_at: Timestamp;
  email_verified_at: Timestamp | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  /** Access token lifetime in seconds. */
  expires_in: number;
}

/** Registration and resend both answer with this, whatever actually happened. */
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

/**
 * A ProseMirror node tree. Deliberately structural rather than a union of the
 * node types in use: Tiptap owns the schema, and a hand-maintained union here
 * would be wrong the first time an extension is added. Shaped to stay
 * assignable to Tiptap's own `JSONContent`, so content crosses that boundary
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

/** The root node. The backend rejects content whose `type` is not `doc`. */
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

/** The 409 body from PATCH, carrying the state the client has to re-sync to. */
export interface DocumentVersionConflict {
  detail: string;
  current: DocumentDetail;
}

/** Every non-2xx response from the API, including FastAPI's own 422s. */
export interface ErrorBody {
  detail: unknown;
}

export const ROLE_RANK: Record<WorkspaceRole, number> = {
  viewer: 1,
  editor: 2,
  owner: 3,
};

export function canEdit(role: WorkspaceRole): boolean {
  return ROLE_RANK[role] >= ROLE_RANK.editor;
}
