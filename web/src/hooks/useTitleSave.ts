import { useCallback, useEffect, useRef, useState } from 'react';

import { asVersionConflict, updateDocument } from '../api/documents';
import { failed, type FailedRequest } from '../api/errors';

export type SaveStatus = 'saved' | 'unsaved' | 'saving' | 'error';

interface UseTitleSaveOptions {
  workspaceId: string;
  documentId: string;
  /** Read at save time. The socket owns the version, not this hook. */
  currentVersion: () => number;
  getTitle: () => string;
  delayMs?: number;
}

export interface TitleSave {
  status: SaveStatus;
  error: FailedRequest | null;
  schedule: () => void;
  isDirty: boolean;
  clearError: () => void;
}

// Unlike content, a title conflict is safe to retry: it overwrites no text.
const MAX_ATTEMPTS = 3;

/** Debounced rename over HTTP. It consumes a version like a socket edit, and
 *  the socket hears of it, so the editor's version stays current. */
export function useTitleSave({
  workspaceId,
  documentId,
  currentVersion,
  getTitle,
  delayMs = 800,
}: UseTitleSaveOptions): TitleSave {
  const [status, setStatus] = useState<SaveStatus>('saved');
  const [error, setError] = useState<FailedRequest | null>(null);

  const timer = useRef<number | null>(null);
  const inFlight = useRef(false);
  const queued = useRef(false);

  const versionRef = useRef(currentVersion);
  versionRef.current = currentVersion;
  const titleRef = useRef(getTitle);
  titleRef.current = getTitle;

  const save = useCallback(async () => {
    if (inFlight.current) {
      queued.current = true;
      return;
    }
    inFlight.current = true;

    try {
      for (;;) {
        queued.current = false;
        setStatus('saving');

        let version = versionRef.current();
        let saved = false;

        for (let attempt = 0; attempt < MAX_ATTEMPTS && !saved; attempt += 1) {
          try {
            await updateDocument(workspaceId, documentId, {
              version,
              title: titleRef.current(),
            });
            saved = true;
          } catch (caught) {
            const conflict = asVersionConflict(caught);
            if (!conflict) {
              setError(failed(caught, 'document.save.failed'));
              setStatus('error');
              return;
            }
            version = conflict.current.version;
          }
        }

        if (!saved) {
          setError(failed(null, 'document.save.failed'));
          setStatus('error');
          return;
        }

        setError(null);
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
      void save();
    }, delayMs);
  }, [save, delayMs]);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  return {
    status,
    error,
    schedule,
    isDirty: status !== 'saved',
    clearError: () => setError(null),
  };
}
