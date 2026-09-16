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
  /** Called at save time, not when the timer is armed, so the request carries
   *  the newest text. */
  getSnapshot: () => DocumentSnapshot;
  /** The server moved on. `replaced` is what the user had locally. */
  onConflict: (conflict: DocumentVersionConflict, replaced: DocumentSnapshot) => void;
  delayMs?: number;
}

export interface Autosave {
  status: SaveStatus;
  error: string | null;
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
 * Debounced save-on-idle. Title and content go in one request because they share
 * a version: two PATCHes would have the second racing the version the first
 * produced.
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

  // Refs so `runSave` stays stable while still seeing fresh callbacks.
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
      // Loops so edits made mid-request are saved straight after it, rather
      // than waiting out another idle period.
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
            // Keep the version: the write never landed, so it is still current.
            setError(errorMessage(caught, 'document.save.failed'));
            setStatus('error');
            return;
          }
          // Anything queued was computed against content about to be replaced.
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
