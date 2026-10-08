import { useCallback, useEffect, useRef, useState } from 'react';

import { cancelQuery, cancelQueryOnUnload, createQuery, streamQuery } from '../api/ai';
import { ApiError } from '../api/client';
import type { AIQuery, AIQueryInput, AIUsage } from '../types/api';

export type AIStreamStatus = 'idle' | 'streaming' | 'done' | 'cancelled' | 'error';

export interface AIStream {
  status: AIStreamStatus;
  query: AIQuery | null;
  /** The tokens so far, then the whole response once done. */
  text: string;
  usage: AIUsage | null;
  /** An ApiError whose `code` errorMessage() translates. */
  error: unknown;
  start: (input: AIQueryInput) => Promise<AIQuery | null>;
  cancel: () => void;
  reset: () => void;
}

const MAX_RESUMES = 5;
const RESUME_DELAY_MS = 600;

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      window.clearTimeout(timer);
      resolve();
    });
  });
}

interface Run {
  queryId: string | null;
  abort: AbortController;
}

/**
 * Create, then stream: a dropped connection resumes the same generation from
 * its last event instead of paying for another. One query at a time.
 *
 * Unmounting, switching document or leaving the page cancels the generation;
 * the server would also stop it once nobody had read it for a few seconds.
 */
export function useAIStream(documentId: string): AIStream {
  const [status, setStatus] = useState<AIStreamStatus>('idle');
  const [query, setQuery] = useState<AIQuery | null>(null);
  const [text, setText] = useState('');
  const [usage, setUsage] = useState<AIUsage | null>(null);
  const [error, setError] = useState<unknown>(null);

  const running = useRef<Run | null>(null);

  const cancel = useCallback(() => {
    const run = running.current;
    if (!run) return;
    running.current = null;
    run.abort.abort();
    // Stops the spend now rather than after the server's grace period.
    if (run.queryId) void cancelQuery(documentId, run.queryId).catch(() => {});
    setStatus('cancelled');
  }, [documentId]);

  const start = useCallback(
    async (input: AIQueryInput): Promise<AIQuery | null> => {
      if (running.current) return null;
      const run: Run = { queryId: null, abort: new AbortController() };
      const { signal } = run.abort;
      running.current = run;
      setStatus('streaming');
      setQuery(null);
      setText('');
      setUsage(null);
      setError(null);

      const end = (next: AIStreamStatus, cause: unknown = null) => {
        if (signal.aborted) return;
        running.current = null;
        setError(cause);
        setStatus(next);
      };

      let created: AIQuery;
      try {
        created = await createQuery(documentId, input);
      } catch (cause) {
        end('error', cause);
        return null;
      }
      if (signal.aborted) {
        // Cancelled while the create was in flight.
        void cancelQuery(documentId, created.id).catch(() => {});
        return null;
      }
      run.queryId = created.id;
      setQuery(created);

      const state: { lastEventId: string | null; outcome: AIStreamStatus | null } = {
        lastEventId: null,
        outcome: null,
      };
      let failure: unknown = null;

      for (let resumes = 0; ; resumes += 1) {
        try {
          await streamQuery(documentId, created.id, {
            signal,
            lastEventId: state.lastEventId,
            onEvent: (event, id) => {
              if (id) state.lastEventId = id;
              switch (event.type) {
                case 'token':
                  setText((current) => current + event.text);
                  return;
                case 'done':
                  setText(event.response);
                  setUsage(event.usage);
                  state.outcome = 'done';
                  return;
                case 'cancelled':
                  state.outcome = 'cancelled';
                  return;
                case 'error':
                  failure = new ApiError(0, event.detail, event, event.code);
                  state.outcome = 'error';
                  return;
              }
            },
          });
        } catch (cause) {
          if (signal.aborted) return created;
          // An HTTP refusal (gone, not ours) will not change on retry.
          if (cause instanceof ApiError) {
            end('error', cause);
            return created;
          }
        }
        if (state.outcome !== null || signal.aborted) break;

        if (resumes >= MAX_RESUMES) {
          failure = new ApiError(0, 'Lost the connection to the response', null, 'ai.stream_lost');
          state.outcome = 'error';
          break;
        }
        await pause(RESUME_DELAY_MS * (resumes + 1), signal);
      }

      end(state.outcome ?? 'cancelled', failure);
      return created;
    },
    [documentId],
  );

  const reset = useCallback(() => {
    if (running.current) return;
    setStatus('idle');
    setQuery(null);
    setText('');
    setUsage(null);
    setError(null);
  }, []);

  useEffect(() => cancel, [cancel]);

  // Unmount does not run when the tab closes or reloads; a keepalive request does.
  useEffect(() => {
    const onPageHide = () => {
      const queryId = running.current?.queryId;
      if (queryId) cancelQueryOnUnload(documentId, queryId);
    };
    window.addEventListener('pagehide', onPageHide);
    return () => window.removeEventListener('pagehide', onPageHide);
  }, [documentId]);

  return { status, query, text, usage, error, start, cancel, reset };
}
