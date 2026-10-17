import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { Editor } from '@tiptap/react';

import { linkText, normalizeHref } from '../editor/links';
import { useI18n } from '../hooks/useI18n';
import { FieldError, invalidProps } from './FieldError';

const INPUT_ID = 'link-href';

interface LinkDialogProps {
  editor: Editor;
  onClose: () => void;
}

/** Edits the link under the cursor, links the selection, or with nothing
 *  selected inserts the address itself as a link. */
export function LinkDialog({ editor, onClose }: LinkDialogProps) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const existing = (editor.getAttributes('link').href as string | undefined) ?? '';
  const [typed, setTyped] = useState(existing);
  const [invalid, setInvalid] = useState(false);

  function close() {
    onClose();
    editor.commands.focus();
  }
  const latestClose = useRef(close);
  latestClose.current = close;

  useEffect(() => {
    input.current?.select();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') latestClose.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  function apply(event: FormEvent) {
    event.preventDefault();
    const href = normalizeHref(typed);
    if (!href) {
      setInvalid(true);
      return;
    }
    const chain = editor.chain().focus();
    if (editor.isActive('link')) {
      chain.extendMarkRange('link').setLink({ href });
    } else if (editor.state.selection.empty) {
      chain.insertContent({ type: 'text', text: linkText(href), marks: [{ type: 'link', attrs: { href } }] });
    } else {
      chain.setLink({ href });
    }
    chain.run();
    onClose();
  }

  function remove() {
    editor.chain().focus().extendMarkRange('link').unsetLink().run();
    onClose();
  }

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <form
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={t('document.link.title')}
        onSubmit={apply}
        noValidate
      >
        <h2 className="modal-title">{t('document.link.title')}</h2>
        <label className="modal-field">
          <span>{t('document.link.urlLabel')}</span>
          <input
            ref={input}
            id={INPUT_ID}
            className="input"
            type="url"
            inputMode="url"
            autoComplete="off"
            placeholder={t('document.link.placeholder')}
            {...invalidProps(INPUT_ID, invalid ? 'document.link.invalid' : null)}
            value={typed}
            onChange={(event) => {
              setTyped(event.target.value);
              setInvalid(false);
            }}
          />
        </label>
        <FieldError inputId={INPUT_ID} problem={invalid ? 'document.link.invalid' : null} />
        <div className="modal-actions">
          {existing && (
            <button type="button" className="btn btn-secondary btn-danger" onClick={remove}>
              {t('document.link.remove')}
            </button>
          )}
          <button type="button" className="btn btn-secondary" onClick={close}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="btn btn-primary">
            {t('document.link.apply')}
          </button>
        </div>
      </form>
    </div>
  );
}
