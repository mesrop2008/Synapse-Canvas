import { useEffect, useRef } from 'react';

import { SHORTCUT_GROUPS, shortcutKeys } from '../editor/shortcuts';
import { useI18n } from '../hooks/useI18n';

export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="modal modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label={t('document.shortcuts.title')}
      >
        <h2 className="modal-title">{t('document.shortcuts.title')}</h2>
        <div className="shortcut-groups">
          {SHORTCUT_GROUPS.map((group) => (
            <section key={group.label}>
              <h3 className="shortcut-group-title">{t(group.label)}</h3>
              <dl className="shortcut-list">
                {group.actions.map((action) => (
                  <div key={action} className="shortcut-row">
                    <dt>{t(`document.shortcuts.actions.${action}`)}</dt>
                    <dd>
                      {shortcutKeys(action, t).map((key, index) => (
                        <kbd key={index}>{key}</kbd>
                      ))}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <div className="modal-actions">
          <button ref={closeButton} type="button" className="btn btn-secondary" onClick={onClose}>
            {t('common.close')}
          </button>
        </div>
      </div>
    </div>
  );
}
