import { useEffect, useRef, useState, type ReactNode } from 'react';

import { useI18n } from '../hooks/useI18n';

interface ConfirmDialogProps {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  /** Require this typed back before confirming. For actions that take other
   *  people's work with them, where a misplaced click is not recoverable. */
  confirmPhrase?: string;
  confirmPhraseLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Not window.confirm: that blocks the event loop, so an autosave in flight
 *  when the dialog opened could not settle underneath it. */
export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  cancelLabel,
  destructive = false,
  confirmPhrase,
  confirmPhraseLabel,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useI18n();
  const confirmButton = useRef<HTMLButtonElement>(null);
  const phraseInput = useRef<HTMLInputElement>(null);
  const [typed, setTyped] = useState('');
  const locked = confirmPhrase !== undefined && typed.trim() !== confirmPhrase;

  useEffect(() => {
    // Focus the field when there is one: focusing a button the user cannot
    // press yet tells them nothing about what to do next.
    (phraseInput.current ?? confirmButton.current)?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onCancel]);

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <h2 className="modal-title">{title}</h2>
        <div className="modal-text">{message}</div>
        {confirmPhrase !== undefined && (
          <label className="modal-field">
            <span>{confirmPhraseLabel}</span>
            <input
              ref={phraseInput}
              className="input"
              type="text"
              autoComplete="off"
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
            />
          </label>
        )}
        <div className="modal-actions">
          <button type="button" className="btn btn-secondary" onClick={onCancel}>
            {cancelLabel ?? t('common.cancel')}
          </button>
          <button
            ref={confirmButton}
            type="button"
            className={`btn ${destructive ? 'btn-secondary btn-danger' : 'btn-primary'}`}
            disabled={locked}
            onClick={onConfirm}
          >
            {confirmLabel ?? t('common.confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}
