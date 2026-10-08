import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Editor, EditorEvents } from '@tiptap/react';

import { applyQuery, type ApplyTarget } from '../api/ai';
import { ApiError } from '../api/client';
import { errorMessage } from '../api/errors';
import { aiUsageKey, useAIUsage } from '../hooks/useAIUsage';
import { useAIStream } from '../hooks/useAIStream';
import { useI18n } from '../hooks/useI18n';
import type { MessageKey } from '../../i18n';
import type { AIQueryInput, AIQueryMode, WorkspaceUsage } from '../types/api';
import { Alert } from './Alert';

const MODES: readonly AIQueryMode[] = ['continue', 'rewrite', 'summarize', 'ask'];

const MODE_LABELS: Record<AIQueryMode, MessageKey> = {
  continue: 'ai.modes.continue',
  rewrite: 'ai.modes.rewrite',
  summarize: 'ai.modes.summarize',
  ask: 'ai.modes.ask',
};
const MODE_HINTS: Record<AIQueryMode, MessageKey> = {
  continue: 'ai.hints.continue',
  rewrite: 'ai.hints.rewrite',
  summarize: 'ai.hints.summarize',
  ask: 'ai.hints.ask',
};
const PLACEHOLDERS: Record<AIQueryMode, MessageKey> = {
  continue: 'ai.placeholders.continue',
  rewrite: 'ai.placeholders.rewrite',
  summarize: 'ai.placeholders.summarize',
  ask: 'ai.placeholders.ask',
};

const SETTLE_TIMEOUT_MS = 2000;

export interface DocumentLink {
  live: boolean;
  isSettled: () => boolean;
  currentVersion: () => number;
}

interface AIPanelProps {
  editor: Editor;
  documentId: string;
  workspaceId: string;
  socket: DocumentLink;
}

/** Where a query's output goes, carried through every later edit -- the
 *  user's own and peers' -- so Insert lands where the query was made. */
interface TrackedRange {
  from: number;
  to: number;
  /** The text it pointed at is gone, e.g. the document was reloaded. */
  lost: boolean;
  moved: boolean;
}

function useTrackedRange(editor: Editor) {
  const range = useRef<TrackedRange | null>(null);

  useEffect(() => {
    // A rebuilt editor shares no history with the old one.
    if (range.current) range.current = { ...range.current, lost: true };

    const onTransaction = ({ transaction }: EditorEvents['transaction']) => {
      const current = range.current;
      if (!current || current.lost || !transaction.docChanged) return;
      const from = transaction.mapping.mapResult(current.from, 1);
      const to = transaction.mapping.mapResult(current.to, -1);
      range.current = {
        from: from.pos,
        to: Math.max(from.pos, to.pos),
        lost: from.deletedAcross && to.deletedAcross,
        moved: true,
      };
    };
    editor.on('transaction', onTransaction);
    return () => {
      editor.off('transaction', onTransaction);
    };
  }, [editor]);

  return range;
}

function useSelectionText(editor: Editor): string {
  const [text, setText] = useState('');

  useEffect(() => {
    const update = () => {
      const { from, to, empty } = editor.state.selection;
      setText(empty ? '' : editor.state.doc.textBetween(from, to, ' '));
    };
    update();
    editor.on('transaction', update);
    return () => {
      editor.off('transaction', update);
    };
  }, [editor]);

  return text;
}

function clip(text: string, length = 90): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > length ? `${flat.slice(0, length)}…` : flat;
}

export function AIPanel({ editor, documentId, workspaceId, socket }: AIPanelProps) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const ai = useAIStream(documentId);
  const usage = useAIUsage(workspaceId);
  const selectionText = useSelectionText(editor);
  const range = useTrackedRange(editor);

  const [mode, setMode] = useState<AIQueryMode>('continue');
  const [instruction, setInstruction] = useState('');
  const [original, setOriginal] = useState('');
  const [inserting, setInserting] = useState(false);
  const [insertProblem, setInsertProblem] = useState<MessageKey | null>(null);
  const [insertError, setInsertError] = useState<unknown>(null);
  const [inserted, setInserted] = useState(false);

  const busy = ai.status === 'streaming';
  const blocked =
    (mode === 'rewrite' && !selectionText.trim()) ||
    (mode === 'ask' && !instruction.trim());

  // The server widens a rewrite that crosses blocks to whole blocks. Its
  // range is adopted unless the document has changed since the request.
  useEffect(() => {
    const query = ai.query;
    const current = range.current;
    if (!query || !current) return;
    if (!current.moved && query.selection_from !== null && query.selection_to !== null) {
      range.current = { ...current, from: query.selection_from, to: query.selection_to };
    }
    if (query.mode === 'rewrite' && range.current) {
      setOriginal(
        editor.state.doc.textBetween(range.current.from, range.current.to, '\n\n'),
      );
    }
  }, [ai.query, editor, range]);

  useEffect(() => {
    if (ai.status !== 'idle' && ai.status !== 'streaming') {
      void queryClient.invalidateQueries({ queryKey: aiUsageKey(workspaceId) });
    }
  }, [ai.status, queryClient, workspaceId]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (busy || blocked) return;
    const { from, to } = editor.state.selection;
    range.current = { from: mode === 'rewrite' ? from : to, to, lost: false, moved: false };
    setOriginal('');
    setInserted(false);
    setInsertProblem(null);
    setInsertError(null);

    const input: AIQueryInput =
      mode === 'rewrite'
        ? { mode, instruction, selection_from: from, selection_to: to }
        : { mode, instruction, selection_to: to };
    await ai.start(input);
  }

  async function settled(): Promise<boolean> {
    const deadline = Date.now() + SETTLE_TIMEOUT_MS;
    while (!socket.isSettled()) {
      if (Date.now() > deadline) return false;
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
    return true;
  }

  async function insert() {
    const query = ai.query;
    if (!query) return;
    setInsertProblem(null);
    setInsertError(null);

    const current = range.current;
    if (query.mode === 'rewrite' && (!current || current.lost)) {
      setInsertProblem('ai.insertErrors.lost');
      return;
    }

    setInserting(true);
    try {
      // The server builds the edit on its copy at this version, which only
      // matches the editor once every local keystroke is acknowledged.
      if (!(await settled())) {
        setInsertProblem('ai.insertErrors.busy');
        return;
      }
      const target: ApplyTarget = { version: socket.currentVersion() };
      if (current && !current.lost) {
        if (query.mode === 'rewrite') target.selection_from = current.from;
        target.selection_to = current.to;
      } else {
        target.selection_to = editor.state.doc.content.size;
      }
      // The change comes back through the socket, as everyone else's does.
      await applyQuery(documentId, query.id, target);
      range.current = null;
      setInserted(true);
      ai.reset();
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'document.stale') {
        setInsertProblem('ai.insertErrors.stale');
      } else {
        setInsertError(cause);
      }
    } finally {
      setInserting(false);
    }
  }

  function discard() {
    range.current = null;
    setOriginal('');
    setInsertProblem(null);
    setInsertError(null);
    ai.reset();
  }

  const number = new Intl.NumberFormat(locale);
  const isRewrite = ai.query?.mode === 'rewrite';
  const empty = ai.status === 'done' && !ai.text.trim();

  return (
    <aside className="ai-panel" aria-label={t('ai.title')}>
      <h2 className="ai-title">{t('ai.title')}</h2>
      <Budget usage={usage.data} />

      <form className="ai-form" onSubmit={submit}>
        <div
          className="segmented ai-modes"
          role="radiogroup"
          aria-label={t('ai.modeLabel')}
        >
          {MODES.map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={mode === option}
              className="segmented-option segmented-text"
              disabled={busy}
              onClick={() => setMode(option)}
            >
              {t(MODE_LABELS[option])}
            </button>
          ))}
        </div>
        <p className="hint">{t(MODE_HINTS[mode])}</p>

        {mode === 'rewrite' && (
          <p className={selectionText ? 'ai-selection' : 'ai-selection muted'}>
            {selectionText
              ? t('ai.selected', { text: clip(selectionText) })
              : t('ai.selectFirst')}
          </p>
        )}

        <textarea
          className="input ai-instruction"
          aria-label={t('ai.instructionLabel')}
          rows={3}
          maxLength={2000}
          value={instruction}
          placeholder={t(PLACEHOLDERS[mode])}
          disabled={busy}
          onChange={(event) => setInstruction(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void submit();
            }
          }}
        />

        <div className="ai-actions">
          {busy && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={ai.cancel}>
              {t('ai.stop')}
            </button>
          )}
          <button
            type="submit"
            className="btn btn-primary btn-sm"
            disabled={busy || blocked}
          >
            {busy ? t('ai.writing') : t('ai.generate')}
          </button>
        </div>
      </form>

      {busy && (
        <div className="ai-output" aria-live="polite" aria-busy="true">
          <p className="ai-text">
            {ai.text}
            <span className="ai-caret" aria-hidden="true" />
          </p>
        </div>
      )}

      {ai.status === 'done' && (
        <div className="ai-output">
          {empty ? (
            <p className="muted">{t('ai.empty')}</p>
          ) : isRewrite ? (
            <div className="ai-compare">
              <section>
                <h3 className="ai-label">{t('ai.original')}</h3>
                <p className="ai-text ai-text-original">{original}</p>
              </section>
              <section>
                <h3 className="ai-label">{t('ai.proposed')}</h3>
                <p className="ai-text">{ai.text}</p>
              </section>
            </div>
          ) : (
            <>
              <h3 className="ai-label">{t('ai.proposed')}</h3>
              <p className="ai-text">{ai.text}</p>
            </>
          )}

          {!empty && (
            <p className="hint">
              {t(isRewrite ? 'ai.insertWhere.rewrite' : 'ai.insertWhere.other')}
            </p>
          )}
          {ai.usage && (
            <p className="ai-usage muted">
              {t('ai.used', {
                tokens: number.format(ai.usage.prompt_tokens + ai.usage.completion_tokens),
                model: ai.usage.model ?? '—',
              })}
            </p>
          )}

          {insertProblem && <Alert kind="warn">{t(insertProblem)}</Alert>}
          {insertError !== null && <Alert>{errorMessage(insertError)}</Alert>}
          {!socket.live && !empty && (
            <p className="hint">{t('ai.insertErrors.offline')}</p>
          )}

          <div className="ai-actions">
            <button type="button" className="btn btn-ghost btn-sm" onClick={discard}>
              {t('ai.discard')}
            </button>
            {!empty && (
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={inserting || !socket.live}
                onClick={() => void insert()}
              >
                {inserting ? t('ai.inserting') : t('ai.insert')}
              </button>
            )}
          </div>
        </div>
      )}

      {ai.status === 'cancelled' && (
        <div className="ai-output">
          <p className="muted">{t('ai.stopped')}</p>
          {ai.text && <p className="ai-text ai-text-original">{ai.text}</p>}
          <div className="ai-actions">
            <button type="button" className="btn btn-ghost btn-sm" onClick={discard}>
              {t('ai.discard')}
            </button>
          </div>
        </div>
      )}

      {ai.status === 'error' && (
        <Alert onDismiss={discard}>{errorMessage(ai.error)}</Alert>
      )}

      {inserted && ai.status === 'idle' && (
        <p className="ai-done" role="status">
          {t('ai.inserted')}
        </p>
      )}
    </aside>
  );
}

function Budget({ usage }: { usage: WorkspaceUsage | undefined }) {
  const { t, locale } = useI18n();
  if (!usage) return null;
  if (usage.daily_token_limit <= 0) {
    return <p className="ai-budget muted">{t('ai.budget.unlimited')}</p>;
  }

  const number = new Intl.NumberFormat(locale);
  const share = Math.min(1, usage.tokens_remaining / usage.daily_token_limit);
  const resets = new Date(usage.resets_at).toLocaleTimeString(locale, {
    hour: '2-digit',
    minute: '2-digit',
  });
  const left = t('ai.budget.left', {
    left: number.format(usage.tokens_remaining),
    limit: number.format(usage.daily_token_limit),
  });

  return (
    <div className="ai-budget" data-low={share < 0.1 ? 'true' : undefined}>
      <div className="ai-budget-row">
        <span>{t('ai.budget.label')}</span>
        <span className="muted">{t('ai.budget.resets', { time: resets })}</span>
      </div>
      <div
        className="ai-meter"
        role="meter"
        aria-label={left}
        aria-valuemin={0}
        aria-valuemax={usage.daily_token_limit}
        aria-valuenow={usage.tokens_remaining}
      >
        <span style={{ width: `${share * 100}%` }} />
      </div>
      <p className="ai-budget-left">{left}</p>
    </div>
  );
}
