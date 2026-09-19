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

// A title conflict is worth retrying, unlike a content one: the PATCH carries
// no content, so replaying it against the newer version cannot overwrite
// anyone's text. It only ever loses to another rename.
const MAX_ATTEMPTS = 3;

/**
 * Debounced rename over HTTP, while the body goes over the WebSocket.
 *
 * Both land in the same row-locked service function on the server and both
 * consume a version, so they cannot race -- and the socket hears about this
 * one like any other edit, which is how the version the next keystroke is
 * based on stays current.
 */
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
