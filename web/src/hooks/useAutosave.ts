import { useCallback, useEffect, useRef, useState } from 'react';

import { asVersionConflict, updateDocument } from '../api/documents';
import { errorMessage } from '../api/errors';
import type { DocumentVersionConflict, ProseMirrorDoc } from '../types/api';

export type SaveStatus = 'saved' | 'unsaved' | 'saving' | 'error';

export interface DocumentSnapshot {
  title: string;
  content: ProseMirrorDoc;
}

interface UseAutosaveOptions {
  workspaceId: string;
  documentId: string;
  /** Version the document was loaded at. The hook owns it from then on. */
  initialVersion: number;
  /** Read the current editor state. Called at save time, never earlier, so the
   *  request always carries the newest text rather than whatever existed when
   *  the timer was armed. */
  getSnapshot: () => DocumentSnapshot;
  /** The server moved on. `replaced` is what the user had locally. */
  onConflict: (conflict: DocumentVersionConflict, replaced: DocumentSnapshot) => void;
  delayMs?: number;
}

export interface Autosave {
  status: SaveStatus;
  error: string | null;
  /** Current server version, for display. */
  version: number;
  /** Restart the idle timer; call on every edit. */
  schedule: () => void;
  /** Save now, cancelling any pending timer. */
  flush: () => Promise<void>;
  /** True while anything is unsaved, in flight, or failed. */
  isDirty: boolean;
  clearError: () => void;
}

/**
 * Debounced save-on-idle for one document.
 *
 * Sends title and content together on every save. They share a single version,
 * so splitting them into two PATCHes would mean the second one racing the
 * version the first just produced -- one request is both simpler and correct.
 */
export function useAutosave({
  workspaceId,
  documentId,
  initialVersion,
  getSnapshot,
  onConflict,
  delayMs = 2000,
}: UseAutosaveOptions): Autosave {
  const [status, setStatus] = useState<SaveStatus>('saved');
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(initialVersion);

  const versionRef = useRef(initialVersion);
  const timer = useRef<number | null>(null);
  const inFlight = useRef(false);
  /** An edit arrived while a request was on the wire. */
  const queued = useRef(false);

  // Kept in refs so `runSave` stays stable while still seeing fresh callbacks.
  const snapshotRef = useRef(getSnapshot);
  snapshotRef.current = getSnapshot;
  const conflictRef = useRef(onConflict);
  conflictRef.current = onConflict;

  const runSave = useCallback(async () => {
    if (inFlight.current) {
      queued.current = true;
      return;
    }
    inFlight.current = true;

    try {
      // Loops rather than returning, so edits made during a request are saved
      // straight after it instead of waiting out another idle period.
      for (;;) {
        queued.current = false;
        setStatus('saving');
        const snapshot = snapshotRef.current();

        try {
          const saved = await updateDocument(workspaceId, documentId, {
            version: versionRef.current,
            title: snapshot.title,
            content: snapshot.content,
          });
          versionRef.current = saved.version;
          setVersion(saved.version);
          setError(null);
        } catch (caught) {
          const conflict = asVersionConflict(caught);
          if (!conflict) {
            // Keep the version: the write never landed, so it is still current
            // and a retry has a real chance of succeeding.
            setError(errorMessage(caught, 'Could not save.'));
            setStatus('error');
            return;
          }
          // The editor is about to be reloaded from the server, so anything
          // queued was computed against content that no longer exists.
          queued.current = false;
          versionRef.current = conflict.current.version;
          setVersion(conflict.current.version);
          conflictRef.current(conflict, snapshot);
          setError(null);
          setStatus('saved');
          return;
        }

        if (!queued.current) {
          setStatus('saved');
          return;
        }
      }
    } finally {
      inFlight.current = false;
    }
  }, [workspaceId, documentId]);

  const schedule = useCallback(() => {
    setStatus('unsaved');
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      void runSave();
    }, delayMs);
  }, [runSave, delayMs]);

  const flush = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    return runSave();
  }, [runSave]);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  return {
    status,
    error,
    version,
    schedule,
    flush,
    // 'saving' counts as dirty: the request may still fail.
    isDirty: status !== 'saved',
    clearError: () => setError(null),
  };
}
